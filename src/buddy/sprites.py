"""ASCII sprite rendering for companion buddies."""

from __future__ import annotations

from .types import (
    CompanionBones, Eye, Hat, Species,
    duck, goose, blob, cat, dragon, octopus, owl, penguin,
    turtle, snail, ghost, axolotl, capybara, cactus, robot,
    rabbit, mushroom, chonk,
)

# Each sprite is 5 lines tall, 12 wide (after {E}->1char substitution).
# Multiple frames per species for idle fidget animation.
BODIES: dict[Species, list[list[str]]] = {
    duck: [
        ['            ', '    __      ', '  <({E} )___  ', '   (  ._>   ', '    `--\u00b4    '],
        ['            ', '    __      ', '  <({E} )___  ', '   (  ._>   ', '    `--\u00b4~   '],
        ['            ', '    __      ', '  <({E} )___  ', '   (  .__>  ', '    `--\u00b4    '],
    ],
    goose: [
        ['            ', '     ({E}>    ', '     ||     ', '   _(__)_   ', '    ^^^^    '],
        ['            ', '    ({E}>     ', '     ||     ', '   _(__)_   ', '    ^^^^    '],
        ['            ', '     ({E}>>   ', '     ||     ', '   _(__)_   ', '    ^^^^    '],
    ],
    blob: [
        ['            ', '   .----.   ', '  ( {E}  {E} )  ', '  (      )  ', '   `----\u00b4   '],
        ['            ', '  .------.  ', ' (  {E}  {E}  ) ', ' (        ) ', '  `------\u00b4  '],
        ['            ', '    .--.    ', '   ({E}  {E})   ', '   (    )   ', '    `--\u00b4    '],
    ],
    cat: [
        ['            ', '   /\\_/\\    ', '  ( {E}   {E})  ', '  (  \u03c9  )   ', '  (")_(")   '],
        ['            ', '   /\\_/\\    ', '  ( {E}   {E})  ', '  (  \u03c9  )   ', '  (")_(")~  '],
        ['            ', '   /\\-/\\    ', '  ( {E}   {E})  ', '  (  \u03c9  )   ', '  (")_(")   '],
    ],
    dragon: [
        ['            ', '  /^\\  /^\\  ', ' <  {E}  {E}  > ', ' (   ~~   ) ', '  `-vvvv-\u00b4  '],
        ['            ', '  /^\\  /^\\  ', ' <  {E}  {E}  > ', ' (        ) ', '  `-vvvv-\u00b4  '],
        ['   ~    ~   ', '  /^\\  /^\\  ', ' <  {E}  {E}  > ', ' (   ~~   ) ', '  `-vvvv-\u00b4  '],
    ],
    octopus: [
        ['            ', '   .----.   ', '  ( {E}  {E} )  ', '  (______)  ', '  /\\/\\/\\/\\  '],
        ['            ', '   .----.   ', '  ( {E}  {E} )  ', '  (______)  ', '  \\/\\/\\/\\/  '],
        ['     o      ', '   .----.   ', '  ( {E}  {E} )  ', '  (______)  ', '  /\\/\\/\\/\\  '],
    ],
    owl: [
        ['            ', '   /\\  /\\   ', '  (({E})({E}))  ', '  (  ><  )  ', '   `----\u00b4   '],
        ['            ', '   /\\  /\\   ', '  (({E})({E}))  ', '  (  ><  )  ', '   .----.   '],
        ['            ', '   /\\  /\\   ', '  (({E})(-))  ', '  (  ><  )  ', '   `----\u00b4   '],
    ],
    penguin: [
        ['            ', '  .---.     ', '  ({E}>{E})     ', ' /(   )\\    ', '  `---\u00b4     '],
        ['            ', '  .---.     ', '  ({E}>{E})     ', ' |(   )|    ', '  `---\u00b4     '],
        ['  .---.     ', '  ({E}>{E})     ', ' /(   )\\    ', '  `---\u00b4     ', '   ~ ~      '],
    ],
    turtle: [
        ['            ', '   _,--._   ', '  ( {E}  {E} )  ', ' /[______]\\ ', '  ``    ``  '],
        ['            ', '   _,--._   ', '  ( {E}  {E} )  ', ' /[______]\\ ', '   ``  ``   '],
        ['            ', '   _,--._   ', '  ( {E}  {E} )  ', ' /[======]\\ ', '  ``    ``  '],
    ],
    snail: [
        ['            ', ' {E}    .--.  ', '  \\  ( @ )  ', '   \\_`--\u00b4   ', '  ~~~~~~~   '],
        ['            ', '  {E}   .--.  ', '  |  ( @ )  ', '   \\_`--\u00b4   ', '  ~~~~~~~   '],
        ['            ', ' {E}    .--.  ', '  \\  ( @  ) ', '   \\_`--\u00b4   ', '   ~~~~~~   '],
    ],
    ghost: [
        ['            ', '   .----.   ', '  / {E}  {E} \\  ', '  |      |  ', '  ~`~``~`~  '],
        ['            ', '   .----.   ', '  / {E}  {E} \\  ', '  |      |  ', '  `~`~~`~`  '],
        ['    ~  ~    ', '   .----.   ', '  / {E}  {E} \\  ', '  |      |  ', '  ~~`~~`~~  '],
    ],
    axolotl: [
        ['            ', '}~(______)~{', '}~({E} .. {E})~{', '  ( .--. )  ', '  (_/  \\_)  '],
        ['            ', '~}(______){~', '~}({E} .. {E}){~', '  ( .--. )  ', '  (_/  \\_)  '],
        ['            ', '}~(______)~{', '}~({E} .. {E})~{', '  (  --  )  ', '  ~_/  \\_~  '],
    ],
    capybara: [
        ['            ', '  n______n  ', ' ( {E}    {E} ) ', ' (   oo   ) ', '  `------\u00b4  '],
        ['            ', '  n______n  ', ' ( {E}    {E} ) ', ' (   Oo   ) ', '  `------\u00b4  '],
        ['    ~  ~    ', '  u______n  ', ' ( {E}    {E} ) ', ' (   oo   ) ', '  `------\u00b4  '],
    ],
    cactus: [
        ['            ', ' n  ____  n ', ' | |{E}  {E}| | ', ' |_|    |_| ', '   |    |   '],
        ['            ', '    ____    ', ' n |{E}  {E}| n ', ' |_|    |_| ', '   |    |   '],
        [' n        n ', ' |  ____  | ', ' | |{E}  {E}| | ', ' |_|    |_| ', '   |    |   '],
    ],
    robot: [
        ['            ', '   .[||].   ', '  [ {E}  {E} ]  ', '  [ ==== ]  ', '  `------\u00b4  '],
        ['            ', '   .[||].   ', '  [ {E}  {E} ]  ', '  [ -==- ]  ', '  `------\u00b4  '],
        ['     *      ', '   .[||].   ', '  [ {E}  {E} ]  ', '  [ ==== ]  ', '  `------\u00b4  '],
    ],
    rabbit: [
        ['            ', '   (\\__/)   ', '  ( {E}  {E} )  ', ' =(  ..  )= ', '  (")__(")  '],
        ['            ', '   (|__/)   ', '  ( {E}  {E} )  ', ' =(  ..  )= ', '  (")__(")  '],
        ['            ', '   (\\__/)   ', '  ( {E}  {E} )  ', ' =( .  . )= ', '  (")__(")  '],
    ],
    mushroom: [
        ['            ', ' .-o-OO-o-. ', '(__________)', '   |{E}  {E}|   ', '   |____|   '],
        ['            ', ' .-O-oo-O-. ', '(__________)', '   |{E}  {E}|   ', '   |____|   '],
        ['   . o  .   ', ' .-o-OO-o-. ', '(__________)', '   |{E}  {E}|   ', '   |____|   '],
    ],
    chonk: [
        ['            ', '  /\\    /\\  ', ' ( {E}    {E} ) ', ' (   ..   ) ', '  `------\u00b4  '],
        ['            ', '  /\\    /|  ', ' ( {E}    {E} ) ', ' (   ..   ) ', '  `------\u00b4  '],
        ['            ', '  /\\    /\\  ', ' ( {E}    {E} ) ', ' (   ..   ) ', '  `------\u00b4~ '],
    ],
}

HAT_LINES: dict[Hat, str] = {
    "none": "",
    "crown": "   \\^^^/    ",
    "tophat": "   [___]    ",
    "propeller": "    -+-     ",
    "halo": "   (   )    ",
    "wizard": "    /^\\     ",
    "beanie": "   (___)    ",
    "tinyduck": "    ,>      ",
}


def render_sprite(bones: CompanionBones, frame: int = 0) -> list[str]:
    """Render a companion sprite at the given animation frame."""
    frames = BODIES[bones.species]
    body = [line.replace("{E}", bones.eye) for line in frames[frame % len(frames)]]
    lines = list(body)

    # Only replace with hat if line 0 is empty
    if bones.hat != "none" and not lines[0].strip():
        lines[0] = HAT_LINES[bones.hat]

    # Drop blank hat slot if all frames have blank line 0
    if not lines[0].strip() and all(not f[0].strip() for f in frames):
        lines.pop(0)

    return lines


def sprite_frame_count(species: Species) -> int:
    """Get the number of animation frames for a species."""
    return len(BODIES[species])


def render_face(bones: CompanionBones) -> str:
    """Render a compact face string for the companion."""
    eye: Eye = bones.eye
    faces = {
        duck: f"({eye}>",
        goose: f"({eye}>",
        blob: f"({eye}{eye})",
        cat: f"={eye}\u03c9{eye}=",
        dragon: f"<{eye}~{eye}>",
        octopus: f"~({eye}{eye})~",
        owl: f"({eye})({eye})",
        penguin: f"({eye}>)",
        turtle: f"[{eye}_{eye}]",
        snail: f"{eye}(@)",
        ghost: f"/{eye}{eye}\\",
        axolotl: f"}}{eye}.{eye}{{",
        capybara: f"({eye}oo{eye})",
        cactus: f"|{eye}  {eye}|",
        robot: f"[{eye}{eye}]",
        rabbit: f"({eye}..{eye})",
        mushroom: f"|{eye}  {eye}|",
        chonk: f"({eye}.{eye})",
    }
    return faces.get(bones.species, f"({eye}{eye})")
