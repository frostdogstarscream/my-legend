// 直接复用现有纯 Dart 模型/规则/求值器，避免 Python 再实现一份状态机。
import 'dart:convert';
import 'dart:io';
import '../../pixel-story-mobile/packages/story_player/lib/src/model.dart';
import '../../pixel-story-mobile/packages/story_player/lib/src/compiler.dart';
import '../../pixel-story-mobile/packages/story_player/lib/src/evaluator.dart';

void main() async {
  try {
    final input = await stdin.transform(utf8.decoder).join();
    final document = StoryDocument.parse(input);
    // 这里只是网页开发预览分页；手机最终加载仍使用真实字体测量并再次校验。
    final manifest = StoryCompiler(layout: const FixedTextLayout()).compile(document);
    final evaluator = const SceneEvaluator();
    final frames = <Map<String, Object?>>[];
    for (var t = 0; t <= manifest.durationMs; t += 100) {
      final s = evaluator.evaluate(manifest, t.toDouble());
      frames.add({'time_ms': t, 'scene_id': s.scene.id, 'background': s.scene.background.name,
        'block_id': s.blockId, 'subtitle': s.subtitle, 'speaker_id': s.speakerId,
        'fade': s.fadeOpacity, 'actors': s.actors.map((a) => {
          ...a.toJson(), 'walk_frame': a.walkFrame, 'offset_x': a.offsetX,
          'offset_y': a.offsetY, 'reaction': a.reaction?.name, 'speaking': a.speaking,
        }).toList()});
    }
    stdout.write(jsonEncode({'ok': true, 'document': document.toJson(),
      'manifest': manifest.toJson(), 'frames': frames}));
  } on StoryError catch (e) {
    stdout.write(jsonEncode({'ok': false, 'error': {'code': e.code,
      'message': e.message, 'field_path': e.fieldPath}}));
  } catch (e) {
    stderr.write(e);
    exitCode = 1;
  }
}
