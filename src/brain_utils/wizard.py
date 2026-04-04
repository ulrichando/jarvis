"""Interactive wizard framework for JARVIS CLI.

Provides a step-by-step wizard that collects user input through text prompts,
choices, confirmations, and multi-select. Supports forward/back navigation,
validation, and cancel.

Provides WizardProvider, WizardNavigationFooter, WizardDialogLayout
as a pure-Python terminal wizard.
"""

import sys
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class WizardStep:
    """A single step in a wizard flow.

    Attributes:
        name: Identifier for this step's collected data.
        prompt_text: Text displayed to the user.
        validator: Optional callable that receives the input and returns
            an error message string if invalid, or empty string / None if OK.
        default: Default value if user presses Enter without input.
        step_type: One of "text", "choice", "confirm", "multi".
        choices: List of (value, label) tuples for choice/multi types.
    """

    name: str
    prompt_text: str
    validator: Callable[[Any], str | None] | None = None
    default: Any = None
    step_type: str = "text"  # "text" | "choice" | "confirm" | "multi"
    choices: list[tuple[str, str]] = field(default_factory=list)


class WizardCancelled(Exception):
    """Raised when the user cancels the wizard."""
    pass


class Wizard:
    """Interactive step-by-step wizard for terminal input.

    Runs a sequence of WizardSteps, collecting user answers into a dict.
    Supports forward/back navigation, per-step validation, and cancel.

    Usage::

        w = Wizard("Setup", [
            WizardStep("name", "Project name:", default="myproject"),
            WizardStep("type", "Project type:", step_type="choice",
                       choices=[("lib", "Library"), ("app", "Application")]),
            WizardStep("confirm", "Proceed?", step_type="confirm", default=True),
        ])
        result = w.run()  # {"name": "foo", "type": "lib", "confirm": True}
    """

    def __init__(self, title: str, steps: list[WizardStep] | None = None) -> None:
        self.title = title
        self.steps: list[WizardStep] = list(steps) if steps else []
        self._data: dict[str, Any] = {}

    def add_step(self, step: WizardStep) -> None:
        """Add a step to the wizard."""
        self.steps.append(step)

    def run(self) -> dict[str, Any]:
        """Run the wizard interactively, returning collected data.

        Prompts the user for each step, allowing back navigation with 'b'
        and cancel with Ctrl+C or 'q'.

        Returns:
            Dict mapping step names to collected values.

        Raises:
            WizardCancelled: If the user cancels the wizard.
        """
        if not self.steps:
            return {}

        self._data = {}
        index = 0

        # Print header
        print(f"\n{'=' * 50}")
        print(f"  {self.title}")
        print(f"{'=' * 50}")

        while 0 <= index < len(self.steps):
            step = self.steps[index]
            progress = f"[{index + 1}/{len(self.steps)}]"

            try:
                value = self._prompt_step(step, progress)
            except (KeyboardInterrupt, EOFError):
                print()
                raise WizardCancelled("Wizard cancelled by user.")

            if value is _BACK_SENTINEL:
                if index > 0:
                    index -= 1
                else:
                    # At first step, cancel
                    raise WizardCancelled("Wizard cancelled by user.")
                continue

            if value is _CANCEL_SENTINEL:
                raise WizardCancelled("Wizard cancelled by user.")

            # Validate
            if step.validator:
                error = step.validator(value)
                if error:
                    print(f"  \033[31mError: {error}\033[0m")
                    continue

            self._data[step.name] = value
            index += 1

        print(f"{'=' * 50}\n")
        return dict(self._data)

    def _prompt_step(self, step: WizardStep, progress: str) -> Any:
        """Prompt for a single step and return the value.

        Returns _BACK_SENTINEL to go back, _CANCEL_SENTINEL to cancel.
        """
        print(f"\n  {progress} {step.prompt_text}")

        if step.step_type == "text":
            return self._prompt_text(step)
        elif step.step_type == "choice":
            return self._prompt_choice(step)
        elif step.step_type == "confirm":
            return self._prompt_confirm(step)
        elif step.step_type == "multi":
            return self._prompt_multi(step)
        else:
            return self._prompt_text(step)

    def _prompt_text(self, step: WizardStep) -> Any:
        """Prompt for free-form text input."""
        default_hint = f" [{step.default}]" if step.default is not None else ""
        hint = f"  (b=back, q=cancel){default_hint}"
        print(hint)

        raw = input("  > ").strip()

        if raw.lower() == "b":
            return _BACK_SENTINEL
        if raw.lower() == "q":
            return _CANCEL_SENTINEL
        if not raw and step.default is not None:
            return step.default
        return raw

    def _prompt_choice(self, step: WizardStep) -> Any:
        """Prompt user to select from a list of choices."""
        if not step.choices:
            return self._prompt_text(step)

        for i, (value, label) in enumerate(step.choices, 1):
            marker = " *" if value == step.default else ""
            print(f"    {i}. {label}{marker}")

        print("  (number to select, b=back, q=cancel)")
        raw = input("  > ").strip()

        if raw.lower() == "b":
            return _BACK_SENTINEL
        if raw.lower() == "q":
            return _CANCEL_SENTINEL

        # Try numeric selection
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(step.choices):
                return step.choices[idx][0]
        except ValueError:
            pass

        # Try matching by value or label
        for value, label in step.choices:
            if raw.lower() in (value.lower(), label.lower()):
                return value

        # Default
        if not raw and step.default is not None:
            return step.default

        print("  \033[31mInvalid selection.\033[0m")
        return self._prompt_choice(step)

    def _prompt_confirm(self, step: WizardStep) -> Any:
        """Prompt for yes/no confirmation."""
        default = step.default if step.default is not None else False
        yn = "Y/n" if default else "y/N"
        print(f"  ({yn}, b=back, q=cancel)")

        raw = input("  > ").strip().lower()

        if raw == "b":
            return _BACK_SENTINEL
        if raw == "q":
            return _CANCEL_SENTINEL
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False

        return default

    def _prompt_multi(self, step: WizardStep) -> Any:
        """Prompt user to select multiple items from a list."""
        if not step.choices:
            return self._prompt_text(step)

        defaults = step.default if isinstance(step.default, (list, set)) else set()

        for i, (value, label) in enumerate(step.choices, 1):
            marker = " [x]" if value in defaults else " [ ]"
            print(f"    {i}.{marker} {label}")

        print("  (comma-separated numbers, b=back, q=cancel)")
        raw = input("  > ").strip()

        if raw.lower() == "b":
            return _BACK_SENTINEL
        if raw.lower() == "q":
            return _CANCEL_SENTINEL

        if not raw:
            return list(defaults) if defaults else []

        selected: list[str] = []
        for part in raw.split(","):
            part = part.strip()
            try:
                idx = int(part) - 1
                if 0 <= idx < len(step.choices):
                    selected.append(step.choices[idx][0])
            except ValueError:
                continue

        return selected


# Internal sentinel values for navigation
class _Sentinel:
    def __init__(self, name: str) -> None:
        self._name = name
    def __repr__(self) -> str:
        return f"<{self._name}>"

_BACK_SENTINEL = _Sentinel("BACK")
_CANCEL_SENTINEL = _Sentinel("CANCEL")
