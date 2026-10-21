import 'dart:math' as math;
import 'package:characters/characters.dart';
import 'model.dart';
import 'templates.dart';

/// 编译器只依赖“怎样分页”的接口，不直接依赖 Flutter 的文字绘制实现。
/// signature 标识字体和排版规则，使编排结果可以追溯到具体的字幕布局。
abstract interface class TextLayoutService {
  List<String> pages(String text);
  String get signature;
}

/// 测试替身：固定每 72 个可见字符一页。正式播放使用 FlutterTextLayout。
class FixedTextLayout implements TextLayoutService {
  const FixedTextLayout();
  @override
  String get signature => 'test_fixed_72';
  @override
  List<String> pages(String text) {
    final chars = text.characters.toList();
    return [
      for (var i = 0; i < chars.length; i += 72)
        chars.sublist(i, math.min(i + 72, chars.length)).join(),
    ];
  }
}

/// 某个时刻的人物姿态，不同于 StoryActor 的固定外观信息。
/// position 是脚下坐标；offset 是点头/摇头产生的临时偏移，不改变站位。
class ActorPose {
  const ActorPose({
    required this.roleId,
    required this.position,
    required this.visible,
    this.facing = Facing.down,
    this.walkFrame = 0,
    this.offsetX = 0,
    this.offsetY = 0,
    this.reaction,
    this.speaking = false,
  });
  final String roleId;
  final Point2 position;
  final bool visible;
  final Facing facing;
  final int walkFrame;
  final double offsetX;
  final double offsetY;
  final Emotion? reaction;
  final bool speaking;
  // 用新对象表达状态变化。注意：reaction 不传时会清空，而不是沿用旧表情。
  ActorPose copy({
    Point2? position,
    bool? visible,
    Facing? facing,
    int? walkFrame,
    double? offsetX,
    double? offsetY,
    Emotion? reaction,
    bool? speaking,
  }) => ActorPose(
    roleId: roleId,
    position: position ?? this.position,
    visible: visible ?? this.visible,
    facing: facing ?? this.facing,
    walkFrame: walkFrame ?? this.walkFrame,
    offsetX: offsetX ?? this.offsetX,
    offsetY: offsetY ?? this.offsetY,
    reaction: reaction,
    speaking: speaking ?? this.speaking,
  );
  // 编排清单只记录基础姿态，不序列化说话状态、动作偏移等逐帧临时效果。
  JsonMap toJson() => {
    'role_id': roleId,
    'position': position.toJson(),
    'visible': visible,
    'direction': facing.name,
  };
}

/// 编译后的剧情块：全剧绝对起止时间 + 已算好的字幕页、移动路径和朝向。
class CompiledBlock {
  CompiledBlock({
    required this.block,
    required this.startMs,
    required this.endMs,
    required Iterable<String> pages,
    this.path,
    this.face,
  }) : pages = List.unmodifiable(pages);
  final StoryBlock block;
  final int startMs;
  final int endMs;
  final List<String> pages;
  final List<Point2>? path;
  final Facing? face;
  JsonMap toJson() => {
    'block': block.toJson(),
    'start_ms': startMs,
    'end_ms': endMs,
    'pages': pages,
    if (path != null) 'path': path!.map((p) => p.toJson()).toList(),
    if (face != null) 'face': face!.name,
  };
}

/// 场景的时间范围与初始人物姿态；求任意时刻的画面都从 initial 开始推导。
class CompiledScene {
  CompiledScene({
    required this.scene,
    required this.startMs,
    required this.endMs,
    required Map<String, ActorPose> initial,
    required Iterable<CompiledBlock> blocks,
  }) : initial = Map.unmodifiable(initial),
       blocks = List.unmodifiable(blocks);
  final StoryScene scene;
  final int startMs;
  final int endMs;
  final Map<String, ActorPose> initial;
  final List<CompiledBlock> blocks;
  JsonMap toJson() => {
    'scene_id': scene.id,
    'start_ms': startMs,
    'end_ms': endMs,
    'initial': initial.map((k, v) => MapEntry(k, v.toJson())),
    'blocks': blocks.map((b) => b.toJson()).toList(),
  };
}

/// 可执行的演出清单，是 StoryDocument 经规则编排后的结果。
/// hash 还包含版本、排版签名等信息，因此它不同于只比较剧本内容的 contentHash。
class StoryManifest {
  StoryManifest({
    required this.document,
    required Iterable<CompiledScene> scenes,
    required this.durationMs,
    required this.layoutSignature,
  }) : scenes = List.unmodifiable(scenes) {
    hash = stableHash(toJson());
  }
  final StoryDocument document;
  final List<CompiledScene> scenes;
  final int durationMs;
  final String layoutSignature;
  late final String hash;
  JsonMap toJson() => {
    'schema_version': '2.0.0',
    'document_id': document.id,
    'revision': document.revision,
    'source_version_id': document.sourceVersion,
    'content_hash': document.contentHash,
    'rules_version': '1.0.0',
    'runtime_version': '0.1.0',
    'style_pack_version': '1.0.0',
    'layout_signature': layoutSignature,
    'duration_ms': durationMs,
    'scenes': scenes.map((s) => s.toJson()).toList(),
  };
}

/// 将“按顺序写好的剧本”变成“可按时间查询的演出清单”，不绘制也不启动时钟。
class StoryCompiler {
  StoryCompiler({required this.layout});
  final TextLayoutService layout;

  StoryManifest compile(StoryDocument input) {
    // 即使调用方直接使用构造函数，也要重新走协议校验，不能绕过输入约束。
    final document = StoryDocument.fromJson(input.toJson());
    final scenes = <CompiledScene>[];
    // 所有场景共用一条毫秒时间轴，不在每个场景开头重新归零。
    var time = 0;
    for (final scene in document.scenes) {
      final initial = <String, ActorPose>{
        for (var i = 0; i < scene.cast.length; i++)
          scene.cast[i]: ActorPose(
            roleId: scene.cast[i],
            position: anchors['slot_${i + 1}']!,
            visible: !scene.hidden.contains(scene.cast[i]),
          ),
      };
      // 编译阶段先模拟每个动作结束后的姿态，用于验证下一条剧情是否合法。
      // 它不是游戏引擎中的角色组件，不能拿来直接绘制。
      final current = Map<String, ActorPose>.of(initial);
      final compiled = <CompiledBlock>[];
      final start = time;
      // 场景开头留 300ms 淡入时间，正文从这之后开始。
      time += 300;
      for (final block in scene.blocks) {
        final role = block.roleId;
        final actor = role == null ? null : current[role]!;
        // 时序校验：入场只能针对隐藏人物，其他人物动作必须由在场人物执行。
        if (actor != null) {
          if (block.action == StoryAction.enter) {
            if (actor.visible) {
              invalid(block.id, '已在场的人物不能再次入场', 'INVALID_ACTOR_STATE');
            }
          } else if (!actor.visible) {
            invalid(block.id, '人物尚未入场或已经离场', 'INVALID_ACTOR_STATE');
          }
        }
        if (block.targetRoleId != null &&
            !current[block.targetRoleId]!.visible) {
          invalid(block.id, '目标人物不可见', 'INVALID_ACTOR_STATE');
        }
        List<Point2>? path;
        Facing? face;
        if ({
          StoryAction.enter,
          StoryAction.exit,
          StoryAction.move,
        }.contains(block.action)) {
          final from = block.action == StoryAction.enter
              ? anchors['entry']!
              : actor!.position;
          final to = block.action == StoryAction.enter
              ? anchors['slot_${scene.cast.indexOf(role!) + 1}']!
              : anchors[block.action == StoryAction.exit
                    ? 'exit'
                    : block.targetAnchor]!;
          // MVP 只检查终点站位是否被占用，并未实现完整寻路或沿途碰撞检测。
          if (current.values.any(
            (a) =>
                a.roleId != role && a.visible && a.position.distanceTo(to) < 1,
          )) {
            invalid(block.id, '目标站位已被占用', 'ANCHOR_OCCUPIED');
          }
          path = List.unmodifiable(pathBetween(from, to));
          current[role!] = actor!.copy(
            position: to,
            visible: block.action != StoryAction.exit,
            facing: samplePath(path, 1).$2,
          );
        } else if (block.kind == BlockKind.dialogue ||
            block.action == StoryAction.face) {
          face =
              block.direction ??
              (block.targetRoleId == null
                  ? Facing.down
                  : toward(
                      actor!.position,
                      current[block.targetRoleId]!.position,
                    ));
          current[role!] = actor!.copy(facing: face);
        }
        // 分页先确定，再检查展示时长，避免对白翻页太快或漏字。
        final pages = block.isText ? layout.pages(block.text!) : <String>[];
        if (block.isText && (pages.isEmpty || pages.join() != block.text)) {
          invalid(block.id, '字幕分页不能遗漏或更改原文');
        }
        final duration = block.durationMs ?? defaultDuration(block);
        if (block.isText && duration < pages.length * 1800) {
          invalid(block.id, '字幕时长不足，每页至少1.8秒', 'TEXT_DURATION_TOO_SHORT');
        }
        // 一个块执行完再开始下一个块；当前版本没有多角色动作并行时间轨道。
        compiled.add(
          CompiledBlock(
            block: block,
            startMs: time,
            endMs: time + duration,
            pages: pages,
            path: path,
            face: face,
          ),
        );
        time += duration;
      }
      // 场景结尾同样留 300ms 淡出时间。
      time += 300;
      scenes.add(
        CompiledScene(
          scene: scene,
          startMs: start,
          endMs: time,
          initial: initial,
          blocks: compiled,
        ),
      );
    }
    if (time > 90000) invalid('duration', '演出超过90秒，请先分段', 'CAPACITY_EXCEEDED');
    return StoryManifest(
      document: document,
      scenes: scenes,
      durationMs: time,
      layoutSignature: layout.signature,
    );
  }

  /// 默认时长是可读性规则，不是 AI 随机决定，也不是依据音频对齐。
  int defaultDuration(StoryBlock block) {
    if (block.isText) {
      // 按每秒 4.2 个可见字符估算阅读时间，加 400ms 缓冲；
      // 向上取整到 100ms，且不少于 1800ms。多页是否足够仍由 compile 检查。
      return math.max(
        1800,
        ((block.text!.characters.length / 4.2 * 1000 + 400) / 100).ceil() * 100,
      );
    }
    return switch (block.action) {
      StoryAction.enter || StoryAction.exit || StoryAction.move => 1200,
      StoryAction.face => 200,
      StoryAction.nod || StoryAction.shakeHead => 700,
      StoryAction.react => 800,
      _ => 500,
    };
  }
}
