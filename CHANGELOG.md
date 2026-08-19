# Changelog

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
