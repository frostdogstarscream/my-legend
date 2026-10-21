import 'package:flutter/material.dart';
import 'controller.dart';
import 'editing.dart';
import 'model.dart';

/// 点击式轻量编辑面板，只修改已有台词、人物外观、背景及反应动作。
/// 它不是剧本生成器，也不创建新的剧情分支。
class StoryEditorView extends StatefulWidget {
  const StoryEditorView({
    super.key,
    required this.controller,
    required this.draft,
  });
  final StoryPlayerController controller;
  final StoryDraft draft;
  @override
  State<StoryEditorView> createState() => _StoryEditorViewState();
}

class _StoryEditorViewState extends State<StoryEditorView> {
  // 面板打开时捕获基准文档；各 Map 按稳定 ID 保存表单值，而不是按显示名字关联。
  late final StoryDocument _base;
  final _texts = <String, TextEditingController>{};
  final _names = <String, TextEditingController>{};
  final _templates = <String, ActorTemplate>{};
  final _colors = <String, String>{};
  final _accessories = <String, String>{};
  final _backgrounds = <String, BackgroundKind>{};
  final _actions = <String, StoryAction>{};
  final _emotions = <String, Emotion>{};
  int _tab = 0;
  // 应用期间禁用按钮和字段，避免重复提交；失败提示保留在面板中便于继续修改。
  bool _busy = false;
  String? _error;
  @override
  void initState() {
    super.initState();
    // 输入先留在表单内，用户打字/选择不会即时替换正在播放的版本。
    _base = widget.draft.base;
    for (final a in _base.actors) {
      _names[a.id] = TextEditingController(text: a.name);
      _templates[a.id] = a.template;
      _colors[a.id] = a.color;
      _accessories[a.id] = a.accessory;
    }
    for (final scene in _base.scenes) {
      _backgrounds[scene.id] = scene.background;
      for (final b in scene.blocks) {
        if (b.isText) _texts[b.id] = TextEditingController(text: b.text);
        if (b.isReaction) _actions[b.id] = b.action!;
        if (b.kind == BlockKind.dialogue ||
            b.action == StoryAction.react ||
            b.isReaction) {
          _emotions[b.id] = b.emotion;
        }
      }
    }
  }

  @override
  void dispose() {
    // TextEditingController 持有监听等资源，面板关闭时必须逐个释放。
    for (final c in [..._texts.values, ..._names.values]) {
      c.dispose();
    }
    super.dispose();
  }

  /// 点击应用时才把表单汇总为新草稿，交给 Controller 校验和编译。
  Future<void> _apply() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      // 每次提交从基准文档重新构造，失败后重试不会沿用上次的半成品草稿。
      final draft = StoryDraft(_base);
      for (final a in _base.actors) {
        draft.updateActor(
          a.id,
          name: _names[a.id]!.text,
          template: _templates[a.id],
          color: _colors[a.id],
          accessory: _accessories[a.id],
        );
      }
      for (final s in _base.scenes) {
        draft.updateBackground(s.id, _backgrounds[s.id]!);
        for (final b in s.blocks) {
          if (b.isText) {
            draft.updateBlock(
              b.id,
              text: _texts[b.id]!.text,
              emotion: b.kind == BlockKind.dialogue ? _emotions[b.id] : null,
            );
          }
          if (b.isReaction) {
            final action = _actions[b.id]!;
            draft.updateBlock(
              b.id,
              action: action,
              emotion: action == StoryAction.react ? _emotions[b.id] : null,
            );
          }
        }
      }
      // 字段编辑和演出发布分开：没有通过编译的修改不能影响当前舞台。
      final result = await widget.controller.applyDraft(draft);
      if (!mounted) return;
      if (result.ok) {
        // 把结果交回 StoryPlayerView，由它在面板关闭后重播最早受影响场景。
        Navigator.pop(context, result);
      } else {
        setState(() => _error = result.error!.message);
      }
    } on StoryError catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _emotion(String id) => DropdownButton<Emotion>(
    value: _emotions[id],
    items: Emotion.values
        .map((e) => DropdownMenuItem(value: e, child: Text(_emotionLabels[e]!)))
        .toList(),
    onChanged: _busy ? null : (e) => setState(() => _emotions[id] = e!),
  );
  Widget _section(String title, List<Widget> children) => Card(
    child: Padding(
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: const TextStyle(fontWeight: FontWeight.w700)),
          const SizedBox(height: 8),
          ...children,
        ],
      ),
    ),
  );

  @override
  Widget build(BuildContext context) {
    final blocks = _base.scenes.expand((s) => s.blocks);
    // 四个选项页是同一份表单的不同视图；切换页签不会丢失已输入内容。
    final content = switch (_tab) {
      0 => <Widget>[
        for (final b in blocks.where((b) => b.isText))
          _section(
            b.roleId == null
                ? '旁白'
                : _base.actors.firstWhere((a) => a.id == b.roleId).name,
            [
              TextField(
                key: ValueKey('text_${b.id}'),
                controller: _texts[b.id],
                maxLength: 500,
                minLines: 1,
                maxLines: 4,
                enabled: !_busy,
                decoration: const InputDecoration(
                  border: OutlineInputBorder(),
                  labelText: '台词内容',
                ),
              ),
              if (b.kind == BlockKind.dialogue) _emotion(b.id),
            ],
          ),
      ],
      1 => <Widget>[
        for (final a in _base.actors)
          _section(a.name, [
            TextField(
              controller: _names[a.id],
              maxLength: 20,
              enabled: !_busy,
              decoration: const InputDecoration(labelText: '显示名字'),
            ),
            Wrap(
              spacing: 6,
              children: [
                for (final t in ActorTemplate.values)
                  ChoiceChip(
                    label: Text(_templateLabels[t]!),
                    selected: _templates[a.id] == t,
                    onSelected: _busy
                        ? null
                        : (_) => setState(() => _templates[a.id] = t),
                  ),
              ],
            ),
            Wrap(
              spacing: 6,
              children: [
                for (final c in ['blue', 'green', 'red', 'brown'])
                  ChoiceChip(
                    label: Text(
                      {
                        'blue': '蓝色',
                        'green': '绿色',
                        'red': '红色',
                        'brown': '棕色',
                      }[c]!,
                    ),
                    selected: _colors[a.id] == c,
                    onSelected: _busy
                        ? null
                        : (_) => setState(() => _colors[a.id] = c),
                  ),
              ],
            ),
            DropdownButton<String>(
              value: _accessories[a.id],
              items: ['none', 'hat', 'beard']
                  .map(
                    (v) => DropdownMenuItem(
                      value: v,
                      child: Text(
                        {'none': '无配饰', 'hat': '帽子', 'beard': '胡子'}[v]!,
                      ),
                    ),
                  )
                  .toList(),
              onChanged: _busy
                  ? null
                  : (v) => setState(() => _accessories[a.id] = v!),
            ),
            const Text('只改变示意外观，不改变人物身份。', style: TextStyle(fontSize: 12)),
          ]),
      ],
      2 => <Widget>[
        for (final s in _base.scenes)
          _section(s.name, [
            Wrap(
              spacing: 6,
              children: [
                for (final b in BackgroundKind.values)
                  ChoiceChip(
                    label: Text(_backgroundLabels[b]!),
                    selected: _backgrounds[s.id] == b,
                    onSelected: _busy
                        ? null
                        : (_) => setState(() => _backgrounds[s.id] = b),
                  ),
              ],
            ),
          ]),
      ],
      _ => <Widget>[
        for (final b in blocks.where((b) => b.isReaction))
          _section(
            '${_base.actors.firstWhere((a) => a.id == b.roleId).name} · ${b.id}',
            [
              Wrap(
                spacing: 6,
                children: [
                  for (final a in [
                    StoryAction.nod,
                    StoryAction.shakeHead,
                    StoryAction.react,
                  ])
                    ChoiceChip(
                      label: Text(
                        {
                          StoryAction.nod: '点头',
                          StoryAction.shakeHead: '摇头',
                          StoryAction.react: '情绪反应',
                        }[a]!,
                      ),
                      selected: _actions[b.id] == a,
                      onSelected: _busy
                          ? null
                          : (_) => setState(() => _actions[b.id] = a),
                    ),
                ],
              ),
              if (_actions[b.id] == StoryAction.react) _emotion(b.id),
            ],
          ),
        if (!blocks.any((b) => b.isReaction)) const Text('本段没有可替换的反应动作。'),
      ],
    };
    return Padding(
      // 为软键盘留出空间，避免手机上编辑台词时底部按钮被遮住。
      padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
      child: SizedBox(
        height: MediaQuery.sizeOf(context).height * .78,
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.all(16),
              child: Row(
                children: [
                  const Expanded(
                    child: Text(
                      '轻微修改演出',
                      style: TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  TextButton(
                    onPressed: _busy ? null : () => Navigator.pop(context),
                    child: const Text('取消'),
                  ),
                ],
              ),
            ),
            Wrap(
              spacing: 6,
              children: [
                for (var i = 0; i < 4; i++)
                  ChoiceChip(
                    label: Text(['台词', '人物', '背景', '表演'][i]),
                    selected: _tab == i,
                    onSelected: _busy ? null : (_) => setState(() => _tab = i),
                  ),
              ],
            ),
            const Padding(
              padding: EdgeInsets.all(8),
              child: Text(
                '应用后从受影响场景开头重播，不重写故事。',
                style: TextStyle(fontSize: 12),
              ),
            ),
            Expanded(
              child: ListView(
                padding: const EdgeInsets.symmetric(horizontal: 12),
                children: content,
              ),
            ),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.all(8),
                child: Text(_error!, style: const TextStyle(color: Colors.red)),
              ),
            Padding(
              padding: const EdgeInsets.all(12),
              child: FilledButton.icon(
                key: const ValueKey('apply_preview'),
                onPressed: _busy ? null : _apply,
                icon: _busy
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.play_arrow),
                label: const Text('应用并预览'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

const _templateLabels = {
  ActorTemplate.adult: '成人',
  ActorTemplate.elder: '长者',
  ActorTemplate.child: '儿童',
  ActorTemplate.animal: '动物',
};
const _backgroundLabels = {
  BackgroundKind.indoor: '室内',
  BackgroundKind.courtyard: '庭院',
  BackgroundKind.outdoor: '户外',
};
const _emotionLabels = {
  Emotion.neutral: '平静',
  Emotion.happy: '高兴',
  Emotion.sad: '悲伤',
  Emotion.angry: '生气',
  Emotion.surprised: '惊讶',
  Emotion.serious: '认真',
};
