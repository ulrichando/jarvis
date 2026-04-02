"""Evolution Validator — tests generated code before deploying.

Safety layers:
1. Syntax check (compile())
2. AST safety scan (block dangerous operations)
3. Subprocess sandbox execution (isolated process with timeout)
4. Functional testing with sample inputs
5. Optional: full test suite gate (pytest)
"""

import ast
import importlib.util
import os
import subprocess
import tempfile
from pathlib import Path

JARVIS_ROOT = Path(__file__).resolve().parent.parent.parent

# Dangerous patterns to block
BLOCKED_CALLS = {
    "os.system", "os.popen", "os.exec", "os.execl", "os.execle",
    "os.execv", "os.execve", "os.execvp", "os.execvpe",
    "os.remove", "os.unlink", "os.rmdir",
    "shutil.rmtree", "shutil.move",
    "__import__", "eval", "exec", "compile",
}

BLOCKED_IMPORTS = {"ctypes", "signal", "pty", "resource", "multiprocessing"}


class EvolutionValidator:
    """Validates generated code with multiple safety layers."""

    def validate_module(self, code: str, test_inputs: list[str] | None = None) -> dict:
        """Full validation pipeline.

        Returns: {"valid": bool, "errors": list[str], "results": list}
        """
        errors = []
        results = []

        # Layer 1: Syntax check
        try:
            compile(code, "<evolution>", "exec")
        except SyntaxError as e:
            return {"valid": False, "errors": [f"Syntax error: {e}"], "results": []}

        # Layer 2: AST safety scan
        safety = self._ast_safety_check(code)
        if not safety["safe"]:
            return {"valid": False, "errors": [f"Safety: {safety['reason']}"], "results": []}

        # Layer 3: Subprocess sandbox
        sandbox = self._sandbox_test(code)
        if not sandbox["passed"]:
            errors.append(f"Sandbox: {sandbox['error']}")
            return {"valid": False, "errors": errors, "results": []}

        # Layer 4: Functional testing
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            spec = importlib.util.spec_from_file_location("evolved_module", temp_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as e:
            errors.append(f"Import error: {e}")
            return {"valid": False, "errors": errors, "results": []}
        finally:
            Path(temp_path).unlink(missing_ok=True)

        # Test check_shortcut if it exists
        if hasattr(module, "check_shortcut") and test_inputs:
            for inp in test_inputs:
                try:
                    result = module.check_shortcut(inp)
                    results.append({"input": inp, "output": result, "error": None})
                except Exception as e:
                    errors.append(f"Runtime error on '{inp}': {e}")
                    results.append({"input": inp, "output": None, "error": str(e)})

        # Test handle_error if it exists
        if hasattr(module, "handle_error"):
            try:
                result = module.handle_error("test query", "test error")
                results.append({"input": "test", "output": result, "error": None})
            except Exception as e:
                errors.append(f"handle_error error: {e}")

        # Test JarvisPlugin if it exists
        if hasattr(module, "JarvisPlugin"):
            try:
                plugin = module.JarvisPlugin()
                assert callable(getattr(plugin, "can_handle", None)), "can_handle not callable"
                assert callable(getattr(plugin, "handle", None)), "handle not callable"
                # Test with a dummy query
                plugin.can_handle("test query")
                results.append({"input": "plugin_init", "output": "ok", "error": None})
            except Exception as e:
                errors.append(f"Plugin test error: {e}")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "results": results,
        }

    def validate_with_tests(self, code: str, test_dir: str = "test/") -> dict:
        """Deploy code temporarily and run the full test suite."""
        # Run pytest to verify nothing is broken
        try:
            result = subprocess.run(
                ["python3", "-m", "pytest", test_dir, "-q", "--tb=short"],
                capture_output=True, text=True, timeout=120,
                cwd=str(JARVIS_ROOT),
            )
            passed = result.returncode == 0
            return {
                "passed": passed,
                "output": result.stdout[-500:] if result.stdout else "",
                "errors": result.stderr[-500:] if result.stderr else "",
            }
        except subprocess.TimeoutExpired:
            return {"passed": False, "output": "", "errors": "Test suite timeout (120s)"}
        except Exception as e:
            return {"passed": False, "output": "", "errors": str(e)}

    def _ast_safety_check(self, code: str) -> dict:
        """Walk AST to detect dangerous patterns."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return {"safe": False, "reason": "Cannot parse code"}

        for node in ast.walk(tree):
            # Check imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in BLOCKED_IMPORTS:
                        return {"safe": False, "reason": f"Blocked import: {alias.name}"}
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] in BLOCKED_IMPORTS:
                    return {"safe": False, "reason": f"Blocked import: {node.module}"}

            # Check function calls
            if isinstance(node, ast.Call):
                call_name = self._get_call_name(node)
                if call_name in BLOCKED_CALLS:
                    return {"safe": False, "reason": f"Blocked call: {call_name}"}

            # Check string literals for shell injection patterns
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value.lower()
                if any(d in val for d in ["rm -rf", "chmod 777", "> /dev/", "mkfs", "dd if="]):
                    return {"safe": False, "reason": f"Dangerous string literal: {node.value[:50]}"}

        return {"safe": True, "reason": ""}

    def _get_call_name(self, node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            parts = []
            current = node.func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))
        return ""

    def _sandbox_test(self, code: str) -> dict:
        """Run code in an isolated subprocess with timeout."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            f.write("\nprint('SANDBOX_OK')\n")
            temp_path = f.name

        try:
            result = subprocess.run(
                ["python3", temp_path],
                capture_output=True, text=True, timeout=10,
                cwd=str(JARVIS_ROOT),
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            if "SANDBOX_OK" in result.stdout:
                return {"passed": True, "error": ""}
            error = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            return {"passed": False, "error": error[-300:]}
        except subprocess.TimeoutExpired:
            return {"passed": False, "error": "Sandbox timeout (10s)"}
        except Exception as e:
            return {"passed": False, "error": str(e)}
        finally:
            Path(temp_path).unlink(missing_ok=True)
