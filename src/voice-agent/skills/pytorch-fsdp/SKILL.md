---
name: pytorch-fsdp
description: Expert guidance for Fully Sharded Data Parallel training with PyTorch FSDP - parameter sharding, mixed precision, CPU offloading, FSDP2
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [torch>=2.0, transformers]
platforms: [linux, macos]
metadata:
  jarvis:
    tags: [Distributed Training, PyTorch, FSDP, Data Parallel, Sharding, Mixed Precision, CPU Offloading, FSDP2, Large-Scale Training]

---

# Pytorch-Fsdp Skill

Comprehensive assistance with pytorch-fsdp development, generated from official documentation.

## When to Use This Skill

This skill should be triggered when:
- Working with pytorch-fsdp
- Asking about pytorch-fsdp features or APIs
- Implementing pytorch-fsdp solutions
- Debugging pytorch-fsdp code
- Learning pytorch-fsdp best practices

## Quick Reference

### Common Patterns

**Pattern 1:** Generic Join Context Manager# Created On: Jun 06, 2025 | Last Updated On: Jun 06, 2025 The generic join context manager facilitates distributed training on uneven inputs. This page outlines the API of the relevant classes: Join, Joinable, and JoinHook. For a tutorial, see Distributed Training with Uneven Inputs Using the Join Context Manager. class torch.distributed.algorithms.Join(joinables, enable=True, throw_on_early_termination=False, **kwargs)[source]# This class defines the generic join context manager, which allows custom hooks to be called after a process joins. These hooks should shadow the collective communications of non-joined processes to prevent hanging and erroring and to ensure algorithmic correctness. Refer to JoinHook for details about the hook definition. Warning The context manager requires each participating Joinable to call the method notify_join_context() before its own per- iteration collective communications to ensure correctness. Warning The context manager requires that all process_group attributes in the JoinHook objects are the same. If there are multiple JoinHook objects, then the device of the first is used. The process group and device information is used for checking for non- joined processes and for notifying processes to throw an exception if throw_on_early_termination is enabled, both of which using an all- reduce. Parameters joinables (List[Joinable]) – a list of the participating Joinable s; their hooks are iterated over in the given order. enable (bool) – a flag enabling uneven input detection; setting to False disables the context manager’s functionality and should only be set when the user knows the inputs will not be uneven (default: True). throw_on_early_termination (bool) – a flag controlling whether to throw an exception upon detecting uneven inputs (default: False). Example: >>> import os >>> import torch >>> import torch.distributed as dist >>> import torch.multiprocessing as mp >>> import torch.nn.parallel.DistributedDataParallel as DDP >>> import torch.distributed.optim.ZeroRedundancyOptimizer as ZeRO >>> from torch.distributed.algorithms.join import Join >>> >>> # On each spawned worker >>> def worker(rank): >>> dist.init_process_group("nccl", rank=rank, world_size=2) >>> model = DDP(torch.nn.Linear(1, 1).to(rank), device_ids=[rank]) >>> optim = ZeRO(model.parameters(), torch.optim.Adam, lr=0.01) >>> # Rank 1 gets one more input than rank 0 >>> inputs = [torch.tensor([1.]).to(rank) for _ in range(10 + rank)] >>> with Join([model, optim]): >>> for input in inputs: >>> loss = model(input).sum() >>> loss.backward() >>> optim.step() >>> # All ranks reach here without hanging/erroring static notify_join_context(joinable)[source]# Notifies the join context manager that the calling process has not yet joined. Then, if throw_on_early_termination=True, checks if uneven inputs have been detected (i.e. if one process has already joined) and throws an exception if so. This method should be called from a Joinable object before its per-iteration collective communications. For example, this should be called at the beginning of the forward pass in DistributedDataParallel. Only the first Joinable object passed into the context manager performs the collective communications in this method, and for the others, this method is vacuous. Parameters joinable (Joinable) – the Joinable object calling this method. Returns An async work handle for the all-reduce meant to notify the context manager that the process has not yet joined if joinable is the first one passed into the context manager; None otherwise. class torch.distributed.algorithms.Joinable[source]# This defines an abstract base class for joinable classes. A joinable class (inheriting from Joinable) should implement join_hook(), which returns a JoinHook instance, in addition to join_device() and join_process_group() that return device and process group information, respectively. abstract property join_device: device# Return the device from which to perform collective communications needed by the join context manager. abstract join_hook(**kwargs)[source]# Return a JoinHook instance for the given Joinable. Parameters kwargs (dict) – a dict containing any keyword arguments to modify the behavior of the join hook at run time; all Joinable instances sharing the same join context manager are forwarded the same value for kwargs. Return type JoinHook abstract property join_process_group: Any# Returns the process group for the collective communications needed by the join context manager itself. class torch.distributed.algorithms.JoinHook[source]# This defines a join hook, which provides two entry points in the join context manager. Entry points : a main hook, which is called repeatedly while there exists a non-joined process, and a post-hook, which is called once all processes have joined. To implement a join hook for the generic join context manager, define a class that inherits from JoinHook and override main_hook() and post_hook() as appropriate. main_hook()[source]# Call this hook while there exists a non-joined process to shadow collective communications in a training iteration. Training iteration i.e., in one forward pass, backward pass, and optimizer step. post_hook(is_last_joiner)[source]# Call hook after all processes have joined. It is passed an additional bool argument is_last_joiner, which indicates if the rank is one of the last to join. Parameters is_last_joiner (bool) – True if the rank is one of the last to join; False otherwise.

```
Join
```

<!-- Truncated: skill exceeded 100k character limit. -->
