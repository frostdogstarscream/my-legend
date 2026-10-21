import 'dart:convert';
import 'model.dart';

/// 独立编辑草稿：JSON 深拷贝避免修改正在播放的不可变剧本。
/// 只开放下面这些字段修改，不开放角色 ID、剧情顺序、增删场景和事件分支。
class StoryDraft {
  StoryDraft(StoryDocument source)
    : base = source,
      _json = object(jsonDecode(jsonEncode(source.toJson())), 'draft');
  final StoryDocument base;
  final JsonMap _json;
  // 读出草稿时再次进行完整结构校验，修改字段不代表它已经可以用于播放。
  StoryDocument get document => StoryDocument.fromJson(_json);

  void updateActor(
    String id, {
    String? name,
    ActorTemplate? template,
    String? color,
    String? accessory,
  }) {
    final actors = array(_json['characters'], 'characters');
    final actor = actors
        .map((x) => object(x, 'actor'))
        .firstWhere(
          (x) => x['role_id'] == id,
          orElse: () => throw const StoryError('INVALID_REFERENCE', '人物不存在'),
        );
    if (name != null) actor['name'] = name;
    if (template != null) actor['template_type'] = template.name;
    if (color != null) actor['color_key'] = color;
    if (accessory != null) actor['accessory'] = accessory;
  }

  void updateBackground(String id, BackgroundKind kind) {
    final scene = array(_json['scenes'], 'scenes')
        .map((x) => object(x, 'scene'))
        .firstWhere(
          (x) => x['scene_id'] == id,
          orElse: () => throw const StoryError('INVALID_REFERENCE', '场景不存在'),
        );
    scene['background_type'] = kind.name;
  }

  void updateBlock(
    String id, {
    String? text,
    StoryAction? action,
    Emotion? emotion,
    double? intensity,
  }) {
    final scenes = array(_json['scenes'], 'scenes');
    final block = scenes
        .expand((x) => array(object(x, 'scene')['blocks'], 'blocks'))
        .map((x) => object(x, 'block'))
        .firstWhere(
          (x) => x['block_id'] == id,
          orElse: () => throw const StoryError('INVALID_REFERENCE', '剧情块不存在'),
        );
    final existing = StoryBlock.fromJson(block, id);
    if (text != null) {
      if (!existing.isText) invalid(id, '只能修改对白和旁白文本');
      if (text != existing.text) {
        block['text'] = text;
        // 文本长度变化后，旧时长可能不足；让编译器依据新文本重新估算。
        block.remove('duration_ms');
      }
    }
    if (action != null) {
      if (!existing.isReaction ||
          !{
            StoryAction.nod,
            StoryAction.shakeHead,
            StoryAction.react,
          }.contains(action)) {
        invalid(id, '只允许替换已有反应动作');
      }
      if (action != existing.action) {
        block['action'] = actionKey(action);
        block.remove('duration_ms');
      }
      // 点头/摇头不接受表情参数，替换动作时清理旧字段以通过严格协议校验。
      if (action != StoryAction.react) {
        block.remove('emotion');
        block.remove('intensity');
      }
    }
    final current = StoryBlock.fromJson(block, id);
    if (emotion != null || intensity != null) {
      if (current.kind != BlockKind.dialogue &&
          current.action != StoryAction.react) {
        invalid(id, '这个剧情块没有情绪参数');
      }
      if (emotion != null) block['emotion'] = emotion.name;
      if (intensity != null) block['intensity'] = intensity;
    }
  }
}

/// sceneId 是最早受影响的场景，供控制器定位并让界面决定是否重播。
class EditResult {
  const EditResult(this.document, this.sceneId, this.changed);
  final StoryDocument document;
  final String sceneId;
  final bool changed;
}

/// 校验草稿基准版本、识别变化范围并生成新版本；真正替换播放内容由控制器负责。
class EditService {
  const EditService();
  EditResult apply(StoryDocument current, StoryDraft draft) {
    // 防止用户打开面板后剧本已更新，却又用旧草稿覆盖了最新内容。
    if (draft.base.id != current.id ||
        draft.base.revision != current.revision ||
        draft.base.contentHash != current.contentHash) {
      throw const StoryError('REVISION_CONFLICT', '内容已更新，请重新打开编辑面板');
    }
    final edited = draft.document;
    // 私有 JSON 只能经受限的 setter 修改；无实际变化则不递增修订号。
    if (edited.contentHash == current.contentHash) {
      return EditResult(current, current.scenes.first.id, false);
    }
    final changedRoles = <String>{};
    for (var i = 0; i < current.actors.length; i++) {
      if (stableHash(current.actors[i].toJson()) !=
          stableHash(edited.actors[i].toJson())) {
        changedRoles.add(current.actors[i].id);
      }
    }
    // 人物外观变化影响其参演场景；背景或剧情块变化影响所在场景。
    // 按原有场景顺序找第一个命中项，而不是总从全剧开头重播。
    var affected = current.scenes.first.id;
    for (var i = 0; i < current.scenes.length; i++) {
      if (current.scenes[i].cast.any(changedRoles.contains) ||
          stableHash(current.scenes[i].toJson()) !=
              stableHash(edited.scenes[i].toJson())) {
        affected = current.scenes[i].id;
        break;
      }
    }
    return EditResult(
      edited.withRevision(current.revision + 1),
      affected,
      true,
    );
  }
}
