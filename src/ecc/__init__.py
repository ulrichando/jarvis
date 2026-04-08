"""JARVIS Error Correction Code (ECC) subsystem.

Provides 4 layers of autonomous self-correction:

  L1: Response quality correction   — re-generate if quality score < threshold
  L2: Tool parameter mutation        — auto-fix common failure patterns in place
  L3: Goal-state verification        — post-task check + one corrective pass
  L4: Cross-session failure injection — past mistakes surfaced into system context
  L5: ECC metrics                    — correction success rates tracked per session
"""
from src.ecc.corrector import ECCorrector
from src.ecc.tool_fixer import ToolFixer
from src.ecc.goal_verifier import GoalVerifier

__all__ = ["ECCorrector", "ToolFixer", "GoalVerifier"]
