# Blender Gothic Tools

Import Gothic II assets straight into Blender. Compiled game files included - no GothicSourcer, no 3ds Max, no conversion step in between.

![Gothic 2 characters dancing in Blender](https://github.com/hoovytubeofficial/BlenderGothicTools/releases/download/v3.17.1/preview.gif)

*(full quality: [Gothic2animation.mp4](https://github.com/hoovytubeofficial/BlenderGothicTools/releases/download/v3.17.1/Gothic2animation.mp4))*

Point it at your Gothic II install once, then pick an NPC script out of the game and get the finished character: armor, head with facial morphs, the right face and body textures, weapons in hand. Works for humans, orcs, monsters and golems. Compiled animations go straight onto the rig.

## What it reads

| Format | What it is |
|---|---|
| `.D` | NPC scripts - assembles the whole character they describe |
| `.MDL` | skeleton + skinned body in one file |
| `.MDM` | skinned body meshes (finds and builds their skeleton automatically) |
| `.MAN` / `.MDH` | compiled animations and skeletons |
| `.MMB` | morph meshes - heads import with expressions and lip-sync visemes as shape keys |
| `.MRM` / `.MSH` / `.ZEN` | compiled item meshes, world meshes, worlds (plus ASCII vob-tree prefabs: props, lights, effects) |
| `.MDS` | model scripts - every animation's name, what follows it, and what happens during it |
| `.3DS` / `.ASC` | the classic modkit source formats, import **and export** |
| `.TEX` | converted to DDS on the fly, mipmaps included |

## Highlights

- **Essemble** - a character builder in the N-panel. Fill in body/head/weapons by hand or load everything from a `.D` script; recipes can be saved and shared as small JSON files.
- **Animations by name, not by filename** - point at a model script, type `RUN` or `DANCE`, and the matching clips import onto your rig end to end. Event tags become timeline markers: footsteps, effects, and the frames where a weapon changes hands.
- **Animation import with retiming** - Gothic clips carry one sample per frame at their own rate (dances at 15 fps, combat at 25). Import them 1:1, or retime to your scene's frame rate with proper eased in-betweens.
- **Rig transposer** - play any animation on any skeleton that shares bone names. Yes, a human dance on a dragon. It measures each joint's turn from rest in world space, so the target stays on its feet.
- **Source/SFM unit preset** for people porting characters to Source Filmmaker.
- **Game-ready naming** - rigs, meshes, materials and textures come in named after their game files, not `Material.001`.
- A **Dance Party** button in the Developer tab. It imports four random villagers, gives each a random dance and builds them a mirror room. It exists because it exercises the entire pipeline in one click, and because it is funny.

## Install

1. Grab the zip from [Releases](../../releases).
2. Blender: `Edit > Preferences > Add-ons > Install from Disk`, pick the zip, enable **Blender Gothic Tools**.
3. In the add-on preferences, set a **master folder** to your Gothic II installation root (the folder containing `_work`, e.g. `C:\Program Files (x86)\Steam\steamapps\common\Gothic II`).

Built and tested on Blender 5.2 with the Steam release of Gothic II: Night of the Raven. Gothic 1 uses the same formats but has not been tested yet.

The add-on currently reads the loose files under `_work/Data`. A vanilla install keeps most of them packed in `.VDF` archives - reading those directly is on the roadmap; until then, extract them once with any VDF tool (e.g. GothicVDFS).

## No game files included

This repository contains code only. Meshes, textures, animations and scripts belong to Piranha Bytes / THQ Nordic and come from your own copy of the game.

## Credits

This project stands on [KrxImpExp](https://gitlab.com/Patrix9999/krximpexp) by **Kerrax**, with the Blender port and years of maintenance by **Patrix**, **Shoun** and **HRY**. The 3DS/ASC/MRM/MSH/ZEN readers are theirs at the core; this fork replaced the UI with native Blender dialogs and added the compiled formats (MDM, MDL, MMB, MAN/MDH, TEX) plus the character assembler on top.

A tip of the hat to **GothicSourcer**, the veteran 3ds Max pipeline that showed what a Gothic toolchain should be able to do, and to the [ZenKit](https://github.com/GothicKit/ZenKit) project, whose format documentation cracked the compiled animation encoding.

## License

GPL-2.0, inherited from KrxImpExp. See [LICENSE](LICENSE).
