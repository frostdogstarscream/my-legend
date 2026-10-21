import 'dart:math' as math;
import 'compiler.dart';
import 'model.dart';
import 'templates.dart';

/// 当前画面的不可变描述：谁在场、站哪里、说什么、是否显示表情及淡入淡出。
/// 这是给渲染层读取的数据，还不是 Flame 组件或 Flutter 控件。
class SceneState {
  SceneState({
    required this.scene,
    required Iterable<ActorPose> actors,
    required this.fadeOpacity,
    this.blockId,
    this.subtitle,
    this.speakerId,
    this.ended = false,
  }) : actors = List.unmodifiable(actors);
  final StoryScene scene;
  final List<ActorPose> actors;
  final double fadeOpacity;
  final String? blockId;
  final String? subtitle;
  final String? speakerId;
  final bool ended;
}

/// 纯计算器：演出清单 + 时间 → 画面状态，不依赖上一帧或真实时钟。
/// 因此暂停、拖到任意时间、重播场景都能得到一致的画面。
class SceneEvaluator {
  const SceneEvaluator();
  SceneState evaluate(StoryManifest manifest, double positionMs) {
    final time = positionMs.clamp(0.0, manifest.durationMs.toDouble());
    final ended = time >= manifest.durationMs;
    // 区间右端不包含 endMs；刚好到场景边界时应进入下一场。
    final scene = manifest.scenes.firstWhere(
      (s) => time < s.endMs,
      orElse: () => manifest.scenes.last,
    );
    // 每次从场景初始姿态重新推导，避免靠帧间累积导致 seek 后人物位置错误。
    final poses = Map<String, ActorPose>.of(scene.initial);
    String? text, speaker, blockId;
    for (final item in scene.blocks) {
      if (time < item.startMs) break;
      final block = item.block;
      // 已结束的块仍贡献最终位置/朝向；只有 active 块显示字幕与临时表演效果。
      final active = time < item.endMs && !ended;
      // 将块内时间转换为 0～1 的进度，结束后固定为 1，移动便停在终点。
      final p = ((time - item.startMs) / (item.endMs - item.startMs)).clamp(
        0.0,
        1.0,
      );
      final role = block.roleId;
      if (role != null) {
        var pose = poses[role]!;
        if (item.path != null) {
          final sample = samplePath(item.path!, p);
          pose = pose.copy(
            position: sample.$1,
            facing: sample.$2,
            visible: block.action != StoryAction.exit || active,
            // 两帧走路精灵按每秒 6 次切换，不取决于设备实际刷新率。
            walkFrame: active
                ? ((time - item.startMs) * 6 / 1000).floor() % 2
                : 0,
          );
        }
        if (item.face != null) pose = pose.copy(facing: item.face);
        if (active) {
          // 一个完整正弦周期模拟点头/摇头，强度决定 1～3 个逻辑像素的幅度。
          final offset = math.sin(p * math.pi * 2) * (1 + block.intensity * 2);
          if (block.action == StoryAction.nod) {
            pose = pose.copy(offsetY: offset);
          }
          if (block.action == StoryAction.shakeHead) {
            pose = pose.copy(offsetX: offset);
          }
          if (block.action == StoryAction.react) {
            pose = pose.copy(reaction: block.emotion);
          }
          if (block.kind == BlockKind.dialogue) {
            pose = pose.copy(
              speaking: true,
              reaction: block.emotion == Emotion.neutral ? null : block.emotion,
            );
          }
        }
        poses[role] = pose;
      }
      if (active) {
        blockId = block.id;
        if (item.pages.isNotEmpty) {
          // 字幕各页平分该块的展示时长；这里不做逐字打字机效果。
          final page = math.min(
            item.pages.length - 1,
            ((time - item.startMs) /
                    ((item.endMs - item.startMs) / item.pages.length))
                .floor(),
          );
          text = item.pages[page];
          speaker = block.kind == BlockKind.dialogue ? role : null;
        }
      }
    }
    // 按脚下 Y 排序决定遮挡关系；同一 Y 用 ID 稳定排序，防止闪烁。
    final sorted = poses.values.toList()
      ..sort((a, b) {
        final order = a.position.y.compareTo(b.position.y);
        return order == 0 ? a.roleId.compareTo(b.roleId) : order;
      });
    // 编译器为每场预留了首尾各 300ms，这里将时间转换成黑色遮罩透明度。
    final fade = ended
        ? 0.0
        : time < scene.startMs + 300
        ? 1 - (time - scene.startMs) / 300
        : time >= scene.endMs - 300
        ? (time - (scene.endMs - 300)) / 300
        : 0.0;
    return SceneState(
      scene: scene.scene,
      actors: sorted,
      fadeOpacity: fade.clamp(0.0, 1.0),
      blockId: blockId,
      subtitle: text,
      speakerId: speaker,
      ended: ended,
    );
  }
}
