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
    import wave
    import numpy as np
    from datasets import Dataset
    from src.speech.voice_collector import load_manifest

    manifest = load_manifest()
    if not manifest:
        print("No training data yet. Use JARVIS with voice to collect data.")
        sys.exit(1)

    # Filter to existing files
    valid = [e for e in manifest if os.path.exists(e["audio_path"])]
    print(f"  {len(valid)} valid samples ({sum(e['duration_s'] for e in valid):.0f}s total)")

    # Load audio arrays directly — avoids torchcodec dependency
    arrays, sample_rates, texts = [], [], []
    for e in valid:
        try:
            with wave.open(e["audio_path"], "rb") as wf:
                sr = wf.getframerate()
                raw = wf.readframes(wf.getnframes())
                arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            arrays.append({"array": arr, "sampling_rate": sr})
            sample_rates.append(sr)
            texts.append(e["text"].lower().strip())
        except Exception as ex:
            print(f"  Skipping {e['audio_path']}: {ex}")

    print(f"  Loaded {len(arrays)} audio files")
    ds = Dataset.from_dict({"audio": arrays, "text": texts})

    # 90/10 split
    split = ds.train_test_split(test_size=0.1, seed=42)
    return split["train"], split["test"]


def train():
    """Run fine-tuning on collected voice data using a manual PyTorch loop.

    Freezes all layers except the decoder attention q_proj/v_proj — effectively
    the same parameter budget as LoRA rank=16 but without PEFT compatibility issues.
    """
    import torch
    from torch.utils.data import DataLoader
    from torch.optim import AdamW
    from transformers import WhisperForConditionalGeneration, WhisperProcessor, get_linear_schedule_with_warmup

    print(f"\n  JARVIS Whisper Fine-Tuning")
    print(f"  ────────────────────────────────────")
    print(f"  Base model: {BASE_MODEL}")
    print(f"  Method:     Selective fine-tune (decoder q/v attention)")
    print(f"  Output:     {MODEL_OUTPUT_DIR}")
    print()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        vram = torch.cuda.get_device_properties(0).total_memory / 1024**3
        print(f"  GPU: {torch.cuda.get_device_name(0)} ({vram:.1f}GB)")
    else:
        print("  WARNING: No GPU — training will be very slow")
    print()

    print("  Loading training data...")
    train_ds, eval_ds = prepare_dataset()
    print(f"  Train: {len(train_ds)} samples, Eval: {len(eval_ds)} samples")

    print("  Loading Whisper model...")
    processor = WhisperProcessor.from_pretrained(BASE_MODEL)
    # Load in fp16 to save VRAM (~1.6GB vs ~3.2GB for fp32)
    model = WhisperForConditionalGeneration.from_pretrained(BASE_MODEL, torch_dtype=torch.float16)
    model.config.forced_decoder_ids = None
    model.config.suppress_tokens = []

    # Freeze everything, then unfreeze decoder attention q_proj / v_proj only
    for param in model.parameters():
        param.requires_grad = False
    for name, param in model.named_parameters():
        if "decoder" in name and ("q_proj" in name or "v_proj" in name):
            param.requires_grad = True

    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"  Trainable: {trainable:,} / {total:,} ({trainable/total*100:.1f}%)")
    model = model.to(device)

    # Cast only trainable params to fp32 so gradients are stable (no GradScaler needed)
    for name, param in model.named_parameters():
        if param.requires_grad:
            param.data = param.data.float()

    # Preprocess dataset
    def prepare_sample(batch):
        audio = batch["audio"]
        feats = processor(audio["array"], sampling_rate=audio["sampling_rate"],
                          return_tensors="pt").input_features[0]
        labels = processor.tokenizer(batch["text"]).input_ids
        return {"input_features": feats, "labels": labels}

    print("  Preprocessing samples...")
    train_ds = train_ds.map(prepare_sample, remove_columns=["audio", "text"])
    eval_ds  = eval_ds.map(prepare_sample, remove_columns=["audio", "text"])
    train_ds.set_format("torch")
    eval_ds.set_format("torch")

    def collate(features):
        feats = torch.stack([f["input_features"] for f in features]).to(device)
        labels = [f["labels"] for f in features]
        max_len = max(l.shape[0] for l in labels)
        padded = torch.full((len(labels), max_len), -100, dtype=torch.long, device=device)
        for i, l in enumerate(labels):
            padded[i, :l.shape[0]] = l
        return {"input_features": feats, "labels": padded}

    BATCH = 4
    ACCUM = 2
    EPOCHS = 3
    WARMUP = 50
    LR = 5e-5

    train_loader = DataLoader(train_ds, batch_size=BATCH, shuffle=True, collate_fn=collate)
    eval_loader  = DataLoader(eval_ds,  batch_size=BATCH, shuffle=False, collate_fn=collate)

    optimizer = AdamW([p for p in model.parameters() if p.requires_grad], lr=LR)
    total_steps = (len(train_loader) // ACCUM) * EPOCHS
    scheduler = get_linear_schedule_with_warmup(optimizer, WARMUP, total_steps)

    MODEL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    best_eval_loss = float("inf")
    global_step = 0

    print(f"\n  Training started — {EPOCHS} epochs × {len(train_loader)} steps\n")
    for epoch in range(EPOCHS):
        model.train()
        train_loss = 0.0
        optimizer.zero_grad()

        for step, batch in enumerate(train_loader):
            # Encoder is fp16, trainable decoder params are fp32 — cast features to fp16
            out = model(input_features=batch["input_features"].half(),
                        labels=batch["labels"])
            loss = out.loss / ACCUM
            loss.backward()
            train_loss += out.loss.item()

            if (step + 1) % ACCUM == 0:
                torch.nn.utils.clip_grad_norm_(
                    [p for p in model.parameters() if p.requires_grad], 1.0)
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad()
                global_step += 1

            if (step + 1) % 10 == 0:
                avg = train_loss / (step + 1)
                pct = (step + 1) / len(train_loader) * 100
                print(f"  Epoch {epoch+1}/{EPOCHS}  step {step+1}/{len(train_loader)} "
                      f"({pct:.0f}%)  loss={avg:.4f}", flush=True)

        # Eval
        model.eval()
        eval_loss = 0.0
        with torch.no_grad():
            for batch in eval_loader:
                out = model(input_features=batch["input_features"].to(dtype),
                            labels=batch["labels"])
                eval_loss += out.loss.item()
        eval_loss /= len(eval_loader)
        train_loss /= len(train_loader)
        print(f"\n  Epoch {epoch+1} done — train_loss={train_loss:.4f}  eval_loss={eval_loss:.4f}")

        if eval_loss < best_eval_loss:
            best_eval_loss = eval_loss
            torch.save({
                "epoch": epoch,
                "eval_loss": eval_loss,
                "state_dict": {k: v for k, v in model.state_dict().items()
                               if any(p is v for p in model.parameters() if p.requires_grad)},
            }, MODEL_OUTPUT_DIR / "best_adapter.pt")
            print(f"  ✓ Saved best checkpoint (eval_loss={eval_loss:.4f})\n")

    # Save full fine-tuned weights and processor
    model.save_pretrained(str(MODEL_OUTPUT_DIR))
    processor.save_pretrained(str(MODEL_OUTPUT_DIR))
    print(f"\n  Model saved to: {MODEL_OUTPUT_DIR}")
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
