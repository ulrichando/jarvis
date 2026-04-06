"""Whisper LoRA Fine-Tuning Pipeline — personalize STT to your voice.

Usage:
    python -m src.speech.finetune_whisper              # Train
    python -m src.speech.finetune_whisper --status      # Check data collection status
    python -m src.speech.finetune_whisper --eval         # Evaluate fine-tuned vs base

Requires: pip install peft transformers datasets torch torchaudio

Architecture:
    - Base: openai/whisper-large-v3-turbo (809M params)
    - LoRA: rank=16, alpha=32, targets q_proj/v_proj in decoder (~3M trainable)
    - Training: 3 epochs, lr=1e-3, batch=4, gradient_accum=2
    - Output: ~/.jarvis/models/whisper-jarvis-lora/
"""

import json
import os
import sys
from pathlib import Path
from src.config import JARVIS_HOME

VOICE_DATA_DIR = JARVIS_HOME / "voice_data"
MODEL_OUTPUT_DIR = JARVIS_HOME / "models" / "whisper-jarvis-lora"
BASE_MODEL = "openai/whisper-large-v3-turbo"


def check_status():
    """Print data collection status."""
    from src.speech.voice_collector import get_collection_status
    s = get_collection_status()
    bar_len = 30
    filled = int(bar_len * s["progress_pct"] / 100)
    bar = "█" * filled + "░" * (bar_len - filled)

    print(f"\n  JARVIS Voice Data Collection")
    print(f"  ────────────────────────────────────")
    print(f"  Samples:  {s['samples']}")
    print(f"  Duration: {s['duration_min']} / {s['target_min']} min")
    print(f"  Progress: {bar} {s['progress_pct']}%")
    print(f"  Ready:    {'YES — run training!' if s['ready_for_training'] else 'Keep talking to JARVIS...'}")
    print(f"  Updated:  {s['last_updated']}")
    print()


def prepare_dataset():
    """Load manifest and create HuggingFace Dataset."""
    from datasets import Dataset, Audio
    from src.speech.voice_collector import load_manifest

    manifest = load_manifest()
    if not manifest:
        print("No training data yet. Use JARVIS with voice to collect data.")
        sys.exit(1)

    # Filter to existing files
    valid = [e for e in manifest if os.path.exists(e["audio_path"])]
    print(f"  {len(valid)} valid samples ({sum(e['duration_s'] for e in valid):.0f}s total)")

    ds = Dataset.from_dict({
        "audio": [e["audio_path"] for e in valid],
        "text": [e["text"].lower().strip() for e in valid],
    })
    ds = ds.cast_column("audio", Audio(sampling_rate=16000))

    # 90/10 split
    split = ds.train_test_split(test_size=0.1, seed=42)
    return split["train"], split["test"]


def train():
    """Run LoRA fine-tuning on collected voice data."""
    import torch
    from transformers import (
        WhisperForConditionalGeneration,
        WhisperProcessor,
        Seq2SeqTrainingArguments,
        Seq2SeqTrainer,
    )
    from peft import LoraConfig, get_peft_model, TaskType

    print(f"\n  JARVIS Whisper Fine-Tuning")
    print(f"  ────────────────────────────────────")
    print(f"  Base model: {BASE_MODEL}")
    print(f"  Method:     LoRA (rank=16, alpha=32)")
    print(f"  Output:     {MODEL_OUTPUT_DIR}")
    print()

    # Check GPU
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        vram = torch.cuda.get_device_properties(0).total_mem / 1024**3
        print(f"  GPU: {torch.cuda.get_device_name(0)} ({vram:.1f}GB)")
    else:
        print("  WARNING: No GPU — training will be very slow")
    print()

    # Load data
    print("  Loading training data...")
    train_ds, eval_ds = prepare_dataset()
    print(f"  Train: {len(train_ds)} samples, Eval: {len(eval_ds)} samples")

    # Load model + processor
    print("  Loading Whisper model...")
    processor = WhisperProcessor.from_pretrained(BASE_MODEL)
    model = WhisperForConditionalGeneration.from_pretrained(
        BASE_MODEL,
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
    )
    model.config.forced_decoder_ids = None
    model.config.suppress_tokens = []
    model.config.use_cache = False  # Required for gradient checkpointing

    # LoRA config — target attention projections in the decoder
    lora_config = LoraConfig(
        task_type=TaskType.SEQ_2_SEQ_LM,
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        target_modules=["q_proj", "v_proj"],
        modules_to_save=["proj_out"],  # Keep output projection trainable
    )
    model = get_peft_model(model, lora_config)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"  Trainable: {trainable:,} / {total:,} ({trainable/total*100:.1f}%)")

    # Prepare data collator
    def prepare_sample(batch):
        audio = batch["audio"]
        inputs = processor(
            audio["array"],
            sampling_rate=audio["sampling_rate"],
            return_tensors="pt",
        )
        batch["input_features"] = inputs.input_features[0]

        labels = processor.tokenizer(batch["text"]).input_ids
        batch["labels"] = labels
        return batch

    train_ds = train_ds.map(prepare_sample, remove_columns=["audio", "text"])
    eval_ds = eval_ds.map(prepare_sample, remove_columns=["audio", "text"])

    # Data collator
    from dataclasses import dataclass
    from typing import Any

    @dataclass
    class DataCollator:
        processor: Any

        def __call__(self, features):
            input_features = torch.stack([
                torch.tensor(f["input_features"]) for f in features
            ])
            labels = [torch.tensor(f["labels"]) for f in features]
            # Pad labels
            label_max = max(len(l) for l in labels)
            padded = torch.full((len(labels), label_max), -100, dtype=torch.long)
            for i, l in enumerate(labels):
                padded[i, :len(l)] = l
            return {"input_features": input_features, "labels": padded}

    # Training args
    MODEL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    training_args = Seq2SeqTrainingArguments(
        output_dir=str(MODEL_OUTPUT_DIR),
        per_device_train_batch_size=4,
        gradient_accumulation_steps=2,
        learning_rate=1e-3,
        warmup_steps=50,
        num_train_epochs=3,
        fp16=(device == "cuda"),
        gradient_checkpointing=True,
        evaluation_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        logging_steps=10,
        report_to="none",
        remove_unused_columns=False,
        label_names=["labels"],
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
    )

    trainer = Seq2SeqTrainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        data_collator=DataCollator(processor),
    )

    print("\n  Training started...\n")
    trainer.train()

    # Save LoRA adapter
    model.save_pretrained(str(MODEL_OUTPUT_DIR))
    processor.save_pretrained(str(MODEL_OUTPUT_DIR))
    print(f"\n  LoRA adapter saved to: {MODEL_OUTPUT_DIR}")
    print("  JARVIS will use it automatically on next restart.")


def evaluate():
    """Compare fine-tuned model vs base on eval set."""
    import torch
    from transformers import WhisperForConditionalGeneration, WhisperProcessor
    from peft import PeftModel

    if not MODEL_OUTPUT_DIR.exists():
        print("No fine-tuned model found. Run training first.")
        return

    _, eval_ds = prepare_dataset()

    processor = WhisperProcessor.from_pretrained(BASE_MODEL)
    device = "cuda" if torch.cuda.is_available() else "cpu"

    # Base model
    print("  Evaluating base model...")
    base_model = WhisperForConditionalGeneration.from_pretrained(
        BASE_MODEL, torch_dtype=torch.float16 if device == "cuda" else torch.float32,
    ).to(device)

    # Fine-tuned model
    print("  Evaluating fine-tuned model...")
    ft_model = WhisperForConditionalGeneration.from_pretrained(
        BASE_MODEL, torch_dtype=torch.float16 if device == "cuda" else torch.float32,
    )
    ft_model = PeftModel.from_pretrained(ft_model, str(MODEL_OUTPUT_DIR)).to(device)

    base_correct, ft_correct, total = 0, 0, 0
    for sample in eval_ds:
        audio = sample["audio"]
        ref = sample["text"].lower().strip()
        inputs = processor(audio["array"], sampling_rate=16000, return_tensors="pt").to(device)

        with torch.no_grad():
            base_ids = base_model.generate(**inputs, max_new_tokens=128)
            ft_ids = ft_model.generate(**inputs, max_new_tokens=128)

        base_text = processor.batch_decode(base_ids, skip_special_tokens=True)[0].lower().strip()
        ft_text = processor.batch_decode(ft_ids, skip_special_tokens=True)[0].lower().strip()

        if base_text == ref:
            base_correct += 1
        if ft_text == ref:
            ft_correct += 1
        total += 1
        print(f"  REF: {ref}")
        print(f"  BASE: {base_text}")
        print(f"  FINE: {ft_text}")
        print()

    print(f"  Base accuracy:      {base_correct}/{total} ({base_correct/max(1,total)*100:.0f}%)")
    print(f"  Fine-tuned accuracy: {ft_correct}/{total} ({ft_correct/max(1,total)*100:.0f}%)")


if __name__ == "__main__":
    if "--status" in sys.argv:
        check_status()
    elif "--eval" in sys.argv:
        evaluate()
    else:
        check_status()
        train()
