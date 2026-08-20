# Changelog

## 3.18.1
- **The two biggest worlds no longer crash Blender.** NewWorld.zen and AddonWorld.zen took
  the whole process down with an access violation inside bmesh. The cause was degenerate
  faces - triangles that name the same vertex twice - being handed to from_pydata, which
  builds a corrupt mesh out of them rather than refusing; the next thing to touch that
  mesh dereferences a null loop. They were being deleted afterwards, which is too late.
  They are now dropped before the mesh is built, along with their material and UV entries.
  NewWorld carries 868 of them and AddonWorld 66; the two worlds that always worked have
  none, which is why this only ever showed up on the big ones.
- Swept the whole retail corpus: 8 .zen, 1351 .mrm, 760 .d, 173 .mdm, 143 .mdl, 61 .mmb,
  50 .asc, 40 .mds and 23 .msh all import without a failure.

## 3.18.0
- **.MDS model script import.** A .MAN file is a nameless block of samples; the model
  script is where a creature's motion is described. Pick a script, type a word to narrow
  the list (RUN, DANCE, 1H), and the matching clips import onto your rig end to end with
  a timeline marker each - instead of guessing which of 3,977 compiled files you wanted.
- Event tags come in as markers: footsteps per ground type, particle effects, and
  DEF_INSERT_ITEM / DEF_REMOVE_ITEM, which is the only record in the game of the frame at
  which a weapon leaves the belt and lands in the hand. Frames are converted out of the
  source file's numbering into the clip's own.
- Scripts also carry what the compiled files cannot: an animation's real name, what
  follows it, which slice of which source it is, and whether it plays in reverse
  (160 of them do).

## 3.17.2
- Soft-skin meshes are now built from their weight table instead of the vertex array
  stored alongside it. The two disagree in 94 of the 119 retail bodies that can be
  checked - HUM_BODY_BABE0 by 15 cm, the dragons by 85, one by over a metre - which is
  what left a character standing next to its own armature. The weights are what the
  engine skins from, so they are the real bind pose.
- The 180 degree turn now reaches the mesh through the rest matrices rather than being
  applied to the vertices by hand, so it can no longer be applied twice or be skipped
  when "Turn 180" is off. Only a model whose skeleton cannot be found still needs it
  applied directly.

## 3.17.1
- Rig transposer now measures joint rotations in world space. Bone-frame deltas turned
  targets about the wrong axes when the two skeletons oriented a shared bone differently -
  a dragon given a human dance ended up folded through the floor.
- New "Keep the Target Upright" option: the root takes the source's turn but not its lean.
- Animation import can retime to the scene's frame rate instead of forcing one key per
  frame at the clip's own rate. Interpolation is picked to match (linear at 1:1, eased
  when retimed) and can be overridden.

## 3.17.0
- `.MDL` import - skeleton and skinned body in one file, needs nothing beside it.
- `.zen` files without a world mesh no longer fail. They are prefabs (a torch, its flame,
  its light); the vob tree now imports as placed meshes, point lights and named empties.
  ASCII archives only for now, the BIN_SAFE worlds still load their world mesh as before.
- Fixed a case bug in the material lookup that crashed on materials with no name in the
  file (GROUND_SLOT.MDL, TREASURE_ADDON_01.MDL).

## 3.16.x
- `.MDM` import: skinned bodies with full weights. The file's skeleton checksum finds the
  matching `.MDH` automatically; rigid attachments (a golem's 23 rocks, a troll's horns)
  are placed at their bone's rest transform.
- Monster and orc `.D` scripts assemble, not just humans - `Mdl_SetVisualBody` is parsed
  alongside `B_SetNpcVisual`.
- Essemble: browse buttons on every field, save/load character recipes as JSON, unit
  scale presets (metres / Source-SFM / custom).
- Material slots renamed after their texture files; duplicates merged.
- Dance Party.

## 3.14.x - 3.15.x
- Fixed the `.MAN` rotation decoder: samples are quantised around midpoint 32767 with a
  2.1/65536 scale, not the obvious (v-0x8000)/0x8000. The old scale could not represent
  rotations past 143°, which is why hips and thighs looked broken in most animations.
- `.MDH` root translation is read, so skeletons no longer sit a metre into the floor.

## 3.8 - 3.13
- `.MAN`/`.MDH` compiled animation import, batch import onto one timeline with markers.
- `.MMB` morph meshes: heads with expressions and visemes as shape keys.
- `.TEX` to DDS conversion.
- Essemble character assembler, `.D` script parsing, texture variant resolution.

## 3.0 - 3.7
- Replaced the external DearPyGui dialogs with native Blender operators; removed the
  dependency installer. The add-on now runs on current Blender with nothing to install.
- Gothic submenus, master-folder texture search, recursive `.tga` indexing.

Earlier history is KrxImpExp itself - see the credits in the README.
