import 'dart:collection';
import 'dart:convert';

import 'package:characters/characters.dart';
import 'package:crypto/crypto.dart';

// 数据层只描述“演什么”，不依赖 Flutter/Flame，也不负责加载素材或绘制画面。
// 阅读顺序：StoryActor → StoryBlock → StoryScene → StoryDocument。
typedef JsonMap = Map<String, Object?>;

enum BlockKind { dialogue, narration, action, pause }

enum ActorTemplate { adult, elder, child, animal }

enum BackgroundKind { indoor, courtyard, outdoor }

// 枚举顺序参与精灵图的帧索引计算；调整顺序时，必须同步调整素材生成器。
enum Facing { down, left, right, up }

enum Emotion { neutral, happy, sad, angry, surprised, serious }

enum StoryAction { enter, exit, move, face, wait, nod, shakeHead, react }

String actionKey(StoryAction value) =>
    value == StoryAction.shakeHead ? 'shake_head' : value.name;

/// 统一错误：code 供程序处理，message 给用户看，fieldPath 定位出错的输入字段。
class StoryError implements Exception {
  const StoryError(this.code, this.message, {this.fieldPath});
  final String code;
  final String message;
  final String? fieldPath;
  @override
  String toString() =>
      '$code: $message${fieldPath == null ? '' : ' ($fieldPath)'}';
}

Never invalid(String path, String message, [String code = 'INVALID_INPUT']) =>
    throw StoryError(code, message, fieldPath: path);

JsonMap object(Object? value, String path) {
  if (value is! Map<String, Object?>) invalid(path, '需要 JSON 对象');
  return value;
}

List<Object?> array(Object? value, String path) {
  if (value is! List<Object?>) invalid(path, '需要数组');
  return value;
}

// 严格拒绝未知字段，避免拼错参数后被静默忽略、画面却没有按预期变化。
void fields(JsonMap map, Set<String> allowed, String path) {
  for (final key in map.keys) {
    if (!allowed.contains(key)) invalid('$path.$key', '不支持的字段');
  }
}

// characters 按可见字符检查长度，避免把组合字或 emoji 拆成多个代码单元。
String stringValue(Object? value, String path, {int max = 64}) {
  if (value is! String ||
      value.trim().isEmpty ||
      value.characters.length > max) {
    invalid(path, '需要非空文本，最多 $max 个字符');
  }
  return value.replaceAll('\r\n', '\n').replaceAll('\r', '\n');
}

// ID 用来关联人物/场景，也用于本地目录名；它不是可随意修改的显示名称。
String identifier(Object? value, String path) {
  final result = stringValue(value, path);
  if (!RegExp(r'^[A-Za-z0-9_-]{1,64}$').hasMatch(result)) {
    invalid(path, 'ID 只能含字母、数字、下划线和连字符');
  }
  return result;
}

T enumValue<T extends Enum>(Object? value, List<T> values, String path) {
  for (final item in values) {
    if (item.name == value) return item;
  }
  invalid(path, '不支持的选项：$value');
}

/// 人物的身份和外观模板；运行时的位置、朝向等另存于 ActorPose。
class StoryActor {
  const StoryActor({
    required this.id,
    required this.name,
    required this.template,
    required this.color,
    this.accessory = 'none',
  });
  final String id;
  final String name;
  final ActorTemplate template;
  final String color;
  final String accessory;

  factory StoryActor.fromJson(JsonMap json, String path) {
    fields(json, {
      'role_id',
      'name',
      'template_type',
      'color_key',
      'accessory',
    }, path);
    final color = stringValue(json['color_key'] ?? 'blue', '$path.color_key');
    if (!{'blue', 'green', 'red', 'brown'}.contains(color)) {
      invalid('$path.color_key', '未知颜色');
    }
    final accessory = stringValue(
      json['accessory'] ?? 'none',
      '$path.accessory',
    );
    if (!{'none', 'hat', 'beard'}.contains(accessory)) {
      invalid('$path.accessory', '未知配饰');
    }
    return StoryActor(
      id: identifier(json['role_id'], '$path.role_id'),
      name: stringValue(json['name'], '$path.name', max: 20),
      template: enumValue(
        json['template_type'] ?? 'adult',
        ActorTemplate.values,
        '$path.template_type',
      ),
      color: color,
      accessory: accessory,
    );
  }
  JsonMap toJson() => {
    'role_id': id,
    'name': name,
    'template_type': template.name,
    'color_key': color,
    'accessory': accessory,
  };
}

/// 最小演出单元：对白、旁白、动作或停顿，按场景内的数组顺序依次执行。
/// durationMs 可省略，之后由编译器估算；此处还没有开始/结束时间。
class StoryBlock {
  const StoryBlock({
    required this.id,
    required this.kind,
    this.roleId,
    this.text,
    this.targetRoleId,
    this.action,
    this.targetAnchor,
    this.direction,
    this.emotion = Emotion.neutral,
    this.intensity = .5,
    this.durationMs,
    this.sourceRef,
  });
  final String id;
  final BlockKind kind;
  final String? roleId;
  final String? text;
  final String? targetRoleId;
  final StoryAction? action;
  final String? targetAnchor;
  final Facing? direction;
  final Emotion emotion;
  final double intensity;
  final int? durationMs;
  final String? sourceRef;

  bool get isText => kind == BlockKind.dialogue || kind == BlockKind.narration;
  bool get isReaction =>
      kind == BlockKind.action &&
      {
        StoryAction.nod,
        StoryAction.shakeHead,
        StoryAction.react,
      }.contains(action);

  factory StoryBlock.fromJson(JsonMap json, String path) {
    final kind = enumValue(json['type'], BlockKind.values, '$path.type');
    StoryAction? action;
    if (kind == BlockKind.action) {
      final key = json['action'];
      for (final a in StoryAction.values) {
        if (actionKey(a) == key) action = a;
      }
      if (action == null) invalid('$path.action', '不支持的动作');
    }
    // 不同类型开放不同参数，例如旁白不需要 role_id，移动才需要 target_anchor。
    final allowed = <String>{'block_id', 'type', 'duration_ms', 'source_ref'};
    if (kind == BlockKind.dialogue) {
      allowed.addAll({
        'role_id',
        'text',
        'target_role_id',
        'emotion',
        'intensity',
      });
    } else if (kind == BlockKind.narration) {
      allowed.add('text');
    } else if (kind == BlockKind.action) {
      allowed.addAll({'role_id', 'action', 'text'});
      if (action == StoryAction.move) allowed.add('target_anchor');
      if (action == StoryAction.face) {
        allowed.addAll({'target_role_id', 'direction'});
      }
      if (action == StoryAction.react) allowed.addAll({'emotion', 'intensity'});
    }
    fields(json, allowed, path);
    final duration = json['duration_ms'];
    if (duration != null &&
        (duration is! int || duration < 100 || duration > 90000)) {
      invalid('$path.duration_ms', '需要 100～90000 的整数毫秒');
    }
    if (kind != BlockKind.dialogue &&
        kind != BlockKind.narration &&
        duration is int &&
        duration > 10000) {
      invalid('$path.duration_ms', '非文本动作最多10秒');
    }
    final rawIntensity = json['intensity'] ?? .5;
    if (rawIntensity is! num ||
        !rawIntensity.isFinite ||
        rawIntensity < 0 ||
        rawIntensity > 1) {
      invalid('$path.intensity', '强度必须为0～1');
    }
    final role = kind == BlockKind.dialogue || kind == BlockKind.action
        ? identifier(json['role_id'], '$path.role_id')
        : null;
    final target = json['target_role_id'] == null
        ? null
        : identifier(json['target_role_id'], '$path.target_role_id');
    final direction = json['direction'] == null
        ? null
        : enumValue(json['direction'], Facing.values, '$path.direction');
    // 两个参数同时缺失或同时出现都非法：转向必须明确“朝某人”或“朝某方向”。
    if (action == StoryAction.face && (target == null) == (direction == null)) {
      invalid(path, '转向需要目标人物或方向，二选一');
    }
    String? anchor;
    if (action == StoryAction.move) {
      anchor = stringValue(json['target_anchor'], '$path.target_anchor');
      if (!{
        'slot_1',
        'slot_2',
        'slot_3',
        'slot_4',
        'entry',
        'exit',
      }.contains(anchor)) {
        invalid('$path.target_anchor', '未知站位');
      }
    }
    if ({
          StoryAction.enter,
          StoryAction.exit,
          StoryAction.move,
        }.contains(action) &&
        duration is int &&
        duration < 300) {
      invalid('$path.duration_ms', '移动至少300毫秒');
    }
    return StoryBlock(
      id: identifier(json['block_id'], '$path.block_id'),
      kind: kind,
      roleId: role,
      text:
          json['text'] == null &&
              kind != BlockKind.dialogue &&
              kind != BlockKind.narration
          ? null
          : stringValue(json['text'], '$path.text', max: 500),
      targetRoleId: target,
      action: action,
      targetAnchor: anchor,
      direction: direction,
      emotion: enumValue(
        json['emotion'] ?? 'neutral',
        Emotion.values,
        '$path.emotion',
      ),
      intensity: rawIntensity.toDouble(),
      durationMs: duration as int?,
      sourceRef: json['source_ref'] == null
          ? null
          : stringValue(json['source_ref'], '$path.source_ref', max: 200),
    );
  }

  JsonMap toJson() => {
    'block_id': id,
    'type': kind.name,
    if (roleId != null) 'role_id': roleId,
    if (text != null) 'text': text,
    if (targetRoleId != null) 'target_role_id': targetRoleId,
    if (action != null) 'action': actionKey(action!),
    if (targetAnchor != null) 'target_anchor': targetAnchor,
    if (direction != null) 'direction': direction!.name,
    if (kind == BlockKind.dialogue || action == StoryAction.react) ...{
      'emotion': emotion.name,
      'intensity': intensity,
    },
    if (durationMs != null) 'duration_ms': durationMs,
    if (sourceRef != null) 'source_ref': sourceRef,
  };
}

/// 一个背景下的连续表演。cast 顺序决定初始站位 slot_1～slot_4。
/// hidden 中的人物已属于本场，但必须通过 enter 动作才能出现在舞台上。
class StoryScene {
  StoryScene({
    required this.id,
    required this.name,
    required this.background,
    required Iterable<String> cast,
    required Iterable<String> hidden,
    required Iterable<StoryBlock> blocks,
    // final 只能禁止变量重新赋值；还要用不可修改集合，防止播放中被外部改动。
  }) : cast = List.unmodifiable(cast),
       hidden = Set.unmodifiable(hidden),
       blocks = List.unmodifiable(blocks);
  final String id;
  final String name;
  final BackgroundKind background;
  final List<String> cast;
  final Set<String> hidden;
  final List<StoryBlock> blocks;

  factory StoryScene.fromJson(JsonMap json, String path) {
    fields(json, {
      'scene_id',
      'name',
      'background_type',
      'cast',
      'initial_hidden_roles',
      'blocks',
    }, path);
    final cast = array(json['cast'], '$path.cast')
        .asMap()
        .entries
        .map((e) => identifier(e.value, '$path.cast[${e.key}]'))
        .toList();
    final hidden = array(
      json['initial_hidden_roles'] ?? <Object?>[],
      '$path.initial_hidden_roles',
    ).map((x) => identifier(x, '$path.initial_hidden_roles')).toList();
    if (cast.isEmpty || cast.length > 4 || cast.toSet().length != cast.length) {
      invalid('$path.cast', '每场1～4人且不可重复', 'CAPACITY_EXCEEDED');
    }
    if (hidden.toSet().length != hidden.length ||
        !hidden.every(cast.contains)) {
      invalid('$path.initial_hidden_roles', '隐藏角色必须属于本场且不可重复');
    }
    final rawBlocks = array(json['blocks'], '$path.blocks');
    if (rawBlocks.isEmpty) invalid('$path.blocks', '场景至少需要一条剧情');
    return StoryScene(
      id: identifier(json['scene_id'], '$path.scene_id'),
      name: stringValue(json['name'], '$path.name', max: 40),
      background: enumValue(
        json['background_type'],
        BackgroundKind.values,
        '$path.background_type',
      ),
      cast: cast,
      hidden: hidden,
      blocks: rawBlocks.asMap().entries.map(
        (e) => StoryBlock.fromJson(
          object(e.value, '$path.blocks[${e.key}]'),
          '$path.blocks[${e.key}]',
        ),
      ),
    );
  }
  JsonMap toJson() => {
    'scene_id': id,
    'name': name,
    'background_type': background.name,
    'cast': cast,
    'initial_hidden_roles': cast.where(hidden.contains).toList(),
    'blocks': blocks.map((x) => x.toJson()).toList(),
  };
}

/// 输入协议的根对象：人物表 + 场景列表 + 文档身份/版本。
/// 这是结构化剧本，不是自然语言生成器，也不是已经渲染好的视频文件。
class StoryDocument {
  StoryDocument({
    required this.id,
    required this.sourceVersion,
    required this.revision,
    required this.title,
    required Iterable<StoryActor> actors,
    required Iterable<StoryScene> scenes,
  }) : actors = List.unmodifiable(actors),
       scenes = List.unmodifiable(scenes);
  final String id;
  final String sourceVersion;
  final int revision;
  final String title;
  final List<StoryActor> actors;
  final List<StoryScene> scenes;

  /// 字符串入口：先限制输入体积，再解析 JSON，最后走统一的字段与引用校验。
  factory StoryDocument.parse(String json) {
    if (utf8.encode(json).length > 1024 * 1024) {
      invalid('document', '输入超过1MiB', 'CAPACITY_EXCEEDED');
    }
    try {
      return StoryDocument.fromJson(object(jsonDecode(json), 'document'));
    } on FormatException {
      throw const StoryError('INVALID_INPUT', 'JSON格式不正确');
    }
  }

  /// 对象入口：协议版本、容量、人物和场景都必须符合当前 MVP 的约束。
  factory StoryDocument.fromJson(JsonMap json) {
    fields(json, {
      'schema_version',
      'document_id',
      'source_version_id',
      'revision',
      'title',
      'timebase',
      'timing_mode',
      'style_pack_id',
      'style_pack_version',
      'subtitle_enabled',
      'characters',
      'scenes',
    }, 'document');
    if (json['schema_version'] != '2.0.0') {
      invalid('schema_version', '只支持2.0.0', 'UNSUPPORTED_SCHEMA');
    }
    if (json['timebase'] != 1000 || json['timing_mode'] != 'visual_estimated') {
      invalid('timing_mode', '仅支持毫秒视觉估算时间');
    }
    if (json['style_pack_id'] != 'basic_pixel' ||
        json['style_pack_version'] != '1.0.0') {
      invalid('style_pack_id', '未知素材包', 'UNKNOWN_TEMPLATE');
    }
    if (json['subtitle_enabled'] != true) {
      invalid('subtitle_enabled', '首版必须显示字幕');
    }
    final revision = json['revision'];
    if (revision is! int || revision < 1) invalid('revision', '修订号必须为正整数');
    final actors = array(json['characters'], 'characters');
    final scenes = array(json['scenes'], 'scenes');
    if (actors.isEmpty ||
        actors.length > 6 ||
        scenes.isEmpty ||
        scenes.length > 2) {
      invalid('document', '最多6人、2场景，且不可为空', 'CAPACITY_EXCEEDED');
    }
    final result = StoryDocument(
      id: identifier(json['document_id'], 'document_id'),
      sourceVersion: identifier(json['source_version_id'], 'source_version_id'),
      revision: revision,
      title: stringValue(json['title'], 'title', max: 80),
      actors: actors.asMap().entries.map(
        (e) => StoryActor.fromJson(
          object(e.value, 'characters[${e.key}]'),
          'characters[${e.key}]',
        ),
      ),
      scenes: scenes.asMap().entries.map(
        (e) => StoryScene.fromJson(
          object(e.value, 'scenes[${e.key}]'),
          'scenes[${e.key}]',
        ),
      ),
    );
    result.validateReferences();
    return result;
  }

  /// 检查 ID 唯一性和跨对象引用；“离场后还能否说话”等时序问题由编译器检查。
  void validateReferences() {
    final roles = actors.map((x) => x.id).toSet();
    if (roles.length != actors.length ||
        scenes.map((x) => x.id).toSet().length != scenes.length) {
      invalid('document', '人物/场景ID重复');
    }
    final blocks = <String>{};
    for (final scene in scenes) {
      if (!scene.cast.every(roles.contains)) {
        invalid(scene.id, '未声明的人物', 'INVALID_REFERENCE');
      }
      for (final block in scene.blocks) {
        if (!blocks.add(block.id)) invalid(block.id, '剧情块ID重复');
        if (block.roleId != null && !scene.cast.contains(block.roleId)) {
          invalid(block.id, '角色不在本场', 'INVALID_REFERENCE');
        }
        if (block.targetRoleId != null &&
            (!scene.cast.contains(block.targetRoleId) ||
                block.targetRoleId == block.roleId)) {
          invalid(block.id, '对话目标错误', 'INVALID_REFERENCE');
        }
      }
    }
    if (blocks.length > 100) invalid('blocks', '最多100条剧情', 'CAPACITY_EXCEEDED');
  }

  JsonMap toJson() => {
    'schema_version': '2.0.0',
    'document_id': id,
    'source_version_id': sourceVersion,
    'revision': revision,
    'title': title,
    'timebase': 1000,
    'timing_mode': 'visual_estimated',
    'style_pack_id': 'basic_pixel',
    'style_pack_version': '1.0.0',
    'subtitle_enabled': true,
    'characters': actors.map((x) => x.toJson()).toList(),
    'scenes': scenes.map((x) => x.toJson()).toList(),
  };

  // 修改版本时创建新对象，不直接改动旧剧本，便于保存快照和排查并发问题。
  StoryDocument withRevision(int value) => StoryDocument(
    id: id,
    sourceVersion: sourceVersion,
    revision: value,
    title: title,
    actors: actors,
    scenes: scenes,
  );
  // 内容哈希刻意排除修订号：判断“内容是否变化”和“版本是否变化”是两件事。
  String get contentHash => stableHash(toJson()..remove('revision'));
}

// Map 的键排序后再计算哈希，避免相同内容因 JSON 字段顺序不同而产生不同结果。
// 数组顺序必须保留，因为人物站位、场景次序和剧情执行顺序都有实际含义。
Object? canonical(Object? value) {
  if (value is Map<String, Object?>) {
    return SplayTreeMap<String, Object?>.from(
      value.map((k, v) => MapEntry(k, canonical(v))),
    );
  }
  if (value is Iterable<Object?>) return value.map(canonical).toList();
  return value;
}

String stableHash(Object? value) =>
    'sha256:${sha256.convert(utf8.encode(jsonEncode(canonical(value))))}';
