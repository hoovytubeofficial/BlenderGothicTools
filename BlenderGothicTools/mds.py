# mds.py: model scripts (.mds) - what the compiled animations actually mean.
# -------------------------------------------------------------------------------------------------------
# Part of Blender Gothic Tools, a continuation of the KrxImpExp project.
# License: GPL
# -------------------------------------------------------------------------------------------------------
# A .MAN file is a nameless block of samples. The model script is where a creature's
# motion is described: what each animation is called, what follows it, which slice of
# which source file it came from, whether it is played in reverse, and - the part nothing
# else in the game records - what HAPPENS during it. Footsteps, particle effects, and the
# frames at which a weapon leaves the belt and lands in the hand.
#
#   Model ("Wolf")
#   {
#       meshAndTree  ("Wol_Body.asc" DONT_USE_MESH)
#       registerMesh ("Warg_Body.ASC")
#       aniEnum
#       {
#           modelTag ("DEF_HIT_LIMB" "BIP01 PONYTAIL1")
#           ani ("s_FistRunL" 1 "s_FistRunL" 0.0 0.0 M. "Wol_RunLoop.ASC" F 9 23)
#           {
#               *eventSFXGrnd (11 "Run")
#           }
#           aniAlias ("t_StumbleB" 1 "" 0.1 0.1 M. "t_FistJumpB" R)
#       }
#   }
#
# The compiled clips are named <SCRIPT FILENAME>-<ANI NAME>.MAN: Wolf.mds's "s_FistRunL"
# is WOLF-S_FISTRUNL.MAN. Not the Model() string - HumanS.mds declares Model("HuS") and
# compiles to HUMANS.*. Checked against the retail files: all 40 scripts name an existing
# .MDH, and every one of HumanS.mds's 784 animations has its .MAN on disk.
# -------------------------------------------------------------------------------------------------------
from .log import klog as print  # route console output through the add-on's log tag
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from . import game_data

_RE_COMMENT = re.compile(r"//[^\n]*")
_RE_TOKEN = re.compile(r'"([^"]*)"|([^\s()]+)')

# Statements we understand. Everything else in a script is skipped rather than guessed at.
_RE_STATEMENT = re.compile(
    r"^\s*(model|meshAndTree|registerMesh|aniEnum|ani|aniAlias|aniBlend|aniComb|"
    r"aniDisable|modelTag|aniSync)\s*\(([^)]*)\)",
    re.IGNORECASE | re.MULTILINE,
)
_RE_EVENT = re.compile(r"\*\s*(event\w+)\s*\(([^)]*)\)", re.IGNORECASE)


def _tokens(text: str) -> List[str]:
    """Split an argument list, keeping quoted strings whole and dropping the quotes.

    finditer, not findall: a non-participating group is None here but an empty STRING
    from findall, and an empty quoted argument is meaningful - it is how an alias says it
    has no follow-up animation."""
    return [match.group(1) if match.group(1) is not None else match.group(2)
            for match in _RE_TOKEN.finditer(text)]


def _number(token: str, default=0):
    try:
        return int(token)
    except (TypeError, ValueError):
        try:
            return float(token)
        except (TypeError, ValueError):
            return default


def compiled_prefix(path) -> str:
    """WOLF for Wolf.mds - the name its compiled skeleton and animations carry."""
    return Path(path).stem.upper()


def _parse_events(block: str) -> List[dict]:
    """The *event... lines inside an animation's { } block, in frame order."""
    events = []
    for match in _RE_EVENT.finditer(block):
        arguments = _tokens(match.group(2))
        if not arguments:
            continue
        events.append({
            "type": match.group(1).lower(),         # eventsfx, eventtag, eventpfx, ...
            "frame": _number(arguments[0]),
            "args": arguments[1:],
        })
    events.sort(key=lambda event: event["frame"])
    return events


def parse_mds(path) -> dict:
    """Read a model script into {model, skeleton, meshes, animations, ...}.

    An animation is {name, layer, next, blend_in, blend_out, flags, source, direction,
    first, last, events, alias_of}. Aliases and blends are kept as animations too - they
    are how a script says "this one is that one backwards", which is a thing the compiled
    clips cannot tell you."""
    text = Path(path).read_text(encoding="Windows-1250", errors="replace")
    text = _RE_COMMENT.sub("", text)

    result = {
        "path": str(path),
        "prefix": compiled_prefix(path),
        "model": "",
        "skeleton": "",
        "meshes": [],
        "animations": [],
        "disabled": [],
        "model_tags": [],
    }

    for match in _RE_STATEMENT.finditer(text):
        keyword = match.group(1).lower()
        arguments = _tokens(match.group(2))
        if not arguments and keyword != "anienum":
            continue

        if keyword == "model":
            result["model"] = arguments[0]
        elif keyword == "meshandtree":
            result["skeleton"] = arguments[0]
        elif keyword == "registermesh":
            result["meshes"].append(arguments[0])
        elif keyword == "modeltag":
            result["model_tags"].append(tuple(arguments))
        elif keyword == "anidisable":
            result["disabled"].append(arguments[0])
        elif keyword in ("ani", "anialias", "aniblend", "anicomb"):
            result["animations"].append(_parse_animation(keyword, arguments, text, match.end()))

    print(f"model script {Path(path).name}: {len(result['animations'])} animation(s), "
          f"{len(result['meshes'])} registered mesh(es), skeleton '{result['skeleton']}'")
    return result


def _parse_animation(keyword: str, arguments: List[str], text: str, after: int) -> dict:
    """One ani / aniAlias / aniBlend / aniComb entry, plus its event block if it has one.

        ani      (name layer next blendIn blendOut flags source dir firstFrame lastFrame)
        aniAlias (name layer next blendIn blendOut flags aliasOf dir)
        aniBlend (name [layer] next blendIn blendOut)
        aniComb  (name layer next blendIn blendOut flags source count)
    """
    animation = {
        "name": arguments[0] if arguments else "",
        "kind": keyword,
        "layer": 1,
        "next": "",
        "blend_in": 0.0,
        "blend_out": 0.0,
        "flags": "",
        "source": "",
        "direction": "F",
        "first": None,
        "last": None,
        "alias_of": "",
        "events": [],
    }

    rest = arguments[1:]
    if keyword == "aniblend":
        # the layer is optional here, so find it by shape rather than by position
        if rest and isinstance(_number(rest[0], None), int) and _number(rest[0], None) is not None:
            animation["layer"] = _number(rest.pop(0), 1)
        if rest:
            animation["next"] = rest.pop(0)
        if len(rest) >= 2:
            animation["blend_in"] = _number(rest[0], 0.0)
            animation["blend_out"] = _number(rest[1], 0.0)
        return animation

    if len(rest) >= 5:
        animation["layer"] = _number(rest[0], 1)
        animation["next"] = rest[1]
        animation["blend_in"] = _number(rest[2], 0.0)
        animation["blend_out"] = _number(rest[3], 0.0)
        animation["flags"] = rest[4]
    if len(rest) >= 6:
        source = rest[5]
        if keyword == "anialias":
            animation["alias_of"] = source
        else:
            animation["source"] = source
    if len(rest) >= 7:
        if keyword == "anicomb":
            animation["first"] = _number(rest[6], None)      # combination count
        else:
            animation["direction"] = rest[6].upper()[:1] or "F"
    if keyword == "ani" and len(rest) >= 9:
        animation["first"] = _number(rest[7], None)
        animation["last"] = _number(rest[8], None)

    # an event block, if the next non-space character opens one
    tail = text[after:after + 4000]
    stripped = tail.lstrip()
    if stripped.startswith("{"):
        start = tail.index("{")
        depth = 0
        for index in range(start, len(tail)):
            if tail[index] == "{":
                depth += 1
            elif tail[index] == "}":
                depth -= 1
                if depth == 0:
                    animation["events"] = _parse_events(tail[start:index])
                    break
    return animation


def resolve_alias(animation: dict, by_name: Dict[str, dict]) -> dict:
    """Follow an alias chain to the entry that actually names a clip."""
    seen = set()
    while animation and animation.get("alias_of"):
        key = animation["alias_of"].upper()
        if key in seen:
            break
        seen.add(key)
        target = by_name.get(key)
        if target is None:
            break
        animation = target
    return animation


def animation_file(script: dict, animation: dict) -> Optional[str]:
    """The compiled .MAN for an entry: <SCRIPT>-<NAME>.MAN, via an alias if need be."""
    by_name = {entry["name"].upper(): entry for entry in script["animations"]}
    for candidate in (animation, resolve_alias(animation, by_name)):
        if not candidate or not candidate.get("name"):
            continue
        path = game_data.find_asset(f"{script['prefix']}-{candidate['name']}.MAN")
        if path:
            return path
    return None


def event_summary(animation: dict) -> str:
    """A short human-readable note of what happens during an animation."""
    if not animation["events"]:
        return ""
    kinds = {}
    for event in animation["events"]:
        kinds[event["type"]] = kinds.get(event["type"], 0) + 1
    return ", ".join(f"{count}x{kind.replace('event', '')}" for kind, count in sorted(kinds.items()))


# Event types that move an item between a body slot and a hand. These are the only record
# in the game of when a weapon is actually drawn - the compiled animation just rotates
# bones, and the item is expected to change parent half way through.
ITEM_EVENTS = {"eventtag"}
ITEM_TAGS = ("DEF_INSERT_ITEM", "DEF_REMOVE_ITEM", "DEF_CREATE_ITEM", "DEF_DESTROY_ITEM")


def item_events(animation: dict) -> List[dict]:
    """The frames at which this animation puts an item into a hand or takes it away."""
    found = []
    for event in animation["events"]:
        if event["type"] not in ITEM_EVENTS or not event["args"]:
            continue
        tag = event["args"][0].upper()
        if tag in ITEM_TAGS:
            found.append({"frame": event["frame"], "tag": tag, "args": event["args"][1:]})
    return found


_CACHE = {"key": None, "script": None}


def load(path) -> Optional[dict]:
    """parse_mds with a one-entry cache, for UI code that re-reads on every redraw."""
    try:
        key = (str(path), os.path.getmtime(path))
    except OSError:
        return None
    if _CACHE["key"] == key:
        return _CACHE["script"]
    try:
        script = parse_mds(path)
    except (OSError, ValueError) as err:
        print(f"{Path(path).name}: {err}", level="WARN")
        script = None
    _CACHE["key"] = key
    _CACHE["script"] = script
    return script
