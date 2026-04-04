"""
Checks if input matches negative or keep-going keyword patterns.
"""

import re


def matches_negative_keyword(input_text: str) -> bool:
    """Checks if input matches negative keyword patterns."""
    lower_input = input_text.lower()

    negative_pattern = re.compile(
        r'\b(wtf|wth|ffs|omfg|shit(ty|tiest)?|dumbass|horrible|awful|'
        r'piss(ed|ing)? off|piece of (shit|crap|junk)|what the (fuck|hell)|'
        r'fucking? (broken|useless|terrible|awful|horrible)|fuck you|'
        r'screw (this|you)|so frustrating|this sucks|damn it)\b'
    )

    return bool(negative_pattern.search(lower_input))


def matches_keep_going_keyword(input_text: str) -> bool:
    """Checks if input matches keep going/continuation patterns."""
    lower_input = input_text.lower().strip()

    # Match "continue" only if it's the entire prompt
    if lower_input == 'continue':
        return True

    # Match "keep going" or "go on" anywhere in the input
    keep_going_pattern = re.compile(r'\b(keep going|go on)\b')
    return bool(keep_going_pattern.search(lower_input))
