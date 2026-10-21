// Pinned evaluator with versioned continuation, home layout and off-stage exit.
import 'dart:convert';
import 'dart:io';
import '__MODEL__';
import '__TEMPLATES__';
import 'compiler.dart';
import 'evaluator.dart';

void main() async {
  try {
    final input = object(jsonDecode(await stdin.transform(utf8.decoder).join()), 'input');
    fields(input, {'document', 'initial_state'}, 'input');
    final document = StoryDocument.fromJson(object(input['document'], 'document'));
    if (document.scenes.length != 1) invalid('scenes', 'segment requires one scene');
    Map<String, ActorPose>? initial;
    if (input['initial_state'] != null) {
      final state = object(input['initial_state'], 'initial_state');
      final scene = document.scenes.single;
      if (state.length != scene.cast.length || !scene.cast.every(state.containsKey)) {
        invalid('initial_state', 'cast does not match carried state');
      }
      initial = <String, ActorPose>{};
      for (final role in scene.cast) {
        final pose = object(state[role], 'initial_state.$role');
        fields(pose, {'role_id', 'position', 'visible', 'direction'}, 'initial_state.$role');
        final position = array(pose['position'], 'position');
        if (pose['role_id'] != role || pose['visible'] is! bool ||
            pose['visible'] != !scene.hidden.contains(role) || position.length != 2 ||
            position.any((v) => v is! num || !v.isFinite) ||
            (((position[0] as num) < 0 || (position[0] as num) > 320 ||
              (position[1] as num) < 0 || (position[1] as num) > 180) &&
             !(pose['visible'] == false && position[0] == __EXIT_X__ && position[1] == __EXIT_Y__)) ||
            !Facing.values.any((v) => v.name == pose['direction'])) {
          invalid('initial_state.$role', 'invalid carried pose');
        }
        initial[role] = ActorPose(roleId: role,
          position: Point2((position[0] as num).toDouble(), (position[1] as num).toDouble()),
          visible: pose['visible'] as bool,
          facing: Facing.values.firstWhere((v) => v.name == pose['direction']));
      }
    }
    final manifest = StoryCompiler(layout: const FixedTextLayout()).compile(document, initialPoses: initial);
    final evaluator = const SceneEvaluator();
    // Include exact block boundaries as well as the original 100ms sample grid.
    final times = <int>{0, manifest.durationMs,
      for (var t = 0; t <= manifest.durationMs; t += 100) t,
      for (final b in manifest.scenes.single.blocks) b.startMs,
      for (final b in manifest.scenes.single.blocks) b.endMs}.toList()..sort();
    final frames = <Map<String, Object?>>[];
    for (final t in times) {
      final s = evaluator.evaluate(manifest, t.toDouble());
      frames.add({'time_ms': t, 'scene_id': s.scene.id, 'background': s.scene.background.name,
        'block_id': s.blockId, 'subtitle': s.subtitle, 'speaker_id': s.speakerId,
        'fade': s.fadeOpacity, 'actors': s.actors.map((a) => {
          ...a.toJson(), 'walk_frame': a.walkFrame, 'offset_x': a.offsetX,
          'offset_y': a.offsetY, 'reaction': a.reaction?.name, 'speaking': a.speaking,
        }).toList()});
    }
    final end = evaluator.evaluate(manifest, manifest.durationMs.toDouble());
    final scene = manifest.scenes.single;
    final holds = <String, Object?>{};
    for (var i = 0; i < scene.blocks.length; i++) {
      final block = scene.blocks[i];
      // Evaluate the completed prefix, so a silent gap cannot start the next action.
      final prefix = StoryManifest(document: document, durationMs: block.endMs,
        layoutSignature: manifest.layoutSignature, scenes: [CompiledScene(scene: scene.scene,
          startMs: scene.startMs, endMs: block.endMs, initial: scene.initial,
          blocks: scene.blocks.take(i+1))]);
      final held = evaluator.evaluate(prefix, block.endMs.toDouble());
      holds[block.block.id] = {'time_ms': block.endMs, 'scene_id': held.scene.id,
        'background': held.scene.background.name, 'block_id': null, 'subtitle': null,
        'speaker_id': null, 'fade': 0.0, 'actors': held.actors.map((a) => {
          ...a.toJson(), 'walk_frame': 0, 'offset_x': 0.0, 'offset_y': 0.0,
          'reaction': null, 'speaking': false}).toList()};
    }
    stdout.write(jsonEncode({'ok': true, 'document': document.toJson(),
      'manifest': manifest.toJson(), 'frames': frames, 'block_end_frames': holds,
      'final_state': {for (final actor in end.actors) actor.roleId: actor.toJson()}}));
  } on StoryError catch (e) {
    stdout.write(jsonEncode({'ok': false, 'error': {'code': e.code, 'field_path': e.fieldPath}}));
  } catch (_) {
    stdout.write(jsonEncode({'ok': false, 'error': {'code': 'SEGMENT_BRIDGE_INVALID'}}));
  }
}
