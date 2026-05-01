"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useCreateProject } from "@/hooks/use-projects";

export function CreateProjectDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const router = useRouter();
  const create = useCreateProject();

  const reset = () => {
    setName("");
    setDescription("");
  };

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    const project = await create.mutateAsync({
      name: trimmed,
      description: description.trim(),
    });
    reset();
    onOpenChange(false);
    router.push(`/projects/${project.id}`);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) reset();
        onOpenChange(v);
      }}
    >
      <DialogContent
        showCloseButton={false}
        className="sm:max-w-md p-6 gap-5"
      >
        <DialogTitle className="font-serif text-2xl font-semibold tracking-tight">
          Create a personal project
        </DialogTitle>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <label
              htmlFor="project-name"
              className="text-[13px] text-muted-foreground"
            >
              What are you working on?
            </label>
            <Input
              id="project-name"
              autoFocus
              placeholder="Name your project"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              className="h-10"
            />
          </div>

          <div className="space-y-1.5">
            <label
              htmlFor="project-desc"
              className="text-[13px] text-muted-foreground"
            >
              What are you trying to achieve?
            </label>
            <Textarea
              id="project-desc"
              placeholder="Describe your project, goals, subject, etc..."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={4}
              className="resize-none min-h-24"
            />
          </div>
        </div>

        <div className="flex justify-end gap-2">
          <DialogClose
            render={
              <Button variant="outline" size="sm" className="rounded-md" />
            }
          >
            Cancel
          </DialogClose>
          <Button
            size="sm"
            variant="outline"
            className="rounded-md bg-foreground text-background hover:bg-foreground/90 hover:text-background border-transparent"
            disabled={!name.trim() || create.isPending}
            onClick={submit}
          >
            {create.isPending ? "Creating…" : "Create project"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
