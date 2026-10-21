import 'dart:async';
import 'package:flame/game.dart';
import 'package:flutter/material.dart';
import 'controller.dart';
import 'editing.dart';
import 'flame_game.dart';
import 'editor_view.dart';

/// 宿主可嵌入的播放器界面：Flame 负责舞台，Flutter 负责字幕、按钮与编辑面板。
/// controller 由宿主创建并最终释放，View 不取得它的所有权。
class StoryPlayerView extends StatefulWidget {
  const StoryPlayerView({
    super.key,
    required this.controller,
    this.enableEditing = true,
  });
  final StoryPlayerController controller;
  final bool enableEditing;
  @override
  State<StoryPlayerView> createState() => _StoryPlayerViewState();
}

class _StoryPlayerViewState extends State<StoryPlayerView> {
  late StoryFlameGame _game;
  late final AppLifecycleListener _lifecycle;
  StoryPlayerController get controller => widget.controller;
  @override
  void initState() {
    super.initState();
    // 引擎实例在初始化时创建，不能每次 build 都新建，否则界面刷新会重置舞台。
    _game = StoryFlameGame(controller);
    controller.playback.addListener(_engineVisibility);
    _lifecycle = AppLifecycleListener(
      onStateChange: (state) {
        controller.setAppLifecycle(state);
        _engineVisibility();
      },
    );
  }

  @override
  void didUpdateWidget(covariant StoryPlayerView oldWidget) {
    super.didUpdateWidget(oldWidget);
    // 宿主换了控制器时解绑旧订阅，并创建对应的新舞台，防止读到旧剧本。
    if (oldWidget.controller != controller) {
      oldWidget.controller.playback.removeListener(_engineVisibility);
      _game.pauseEngine();
      _game.removeAll(_game.children);
      _game = StoryFlameGame(controller);
      controller.playback.addListener(_engineVisibility);
    }
  }

  // 是否运行引擎帧循环，与是否推进剧情时间不同：暂停剧情仍可渲染当前画面。
  void _engineVisibility() {
    if (controller.canRender) {
      _game.resumeEngine();
    } else {
      _game.pauseEngine();
    }
  }

  @override
  // View 只清理自己的监听和引擎；Controller 的 dispose 留给创建它的宿主。
  void dispose() {
    controller.playback.removeListener(_engineVisibility);
    _lifecycle.dispose();
    _game.pauseEngine();
    super.dispose();
  }

  Future<void> _edit() async {
    final document = controller.document;
    if (document == null) return;
    // 编辑前冻结时间，并基于当前不可变文档创建草稿；取消不会修改原剧本。
    controller.pause();
    final result = await showModalBottomSheet<ApplyResult>(
      context: context,
      isScrollControlled: true,
      // 面板通过明确的“取消/应用”按钮关闭，不依赖拖拽或点击外部区域。
      enableDrag: false,
      isDismissible: false,
      useSafeArea: true,
      builder: (_) =>
          StoryEditorView(controller: controller, draft: StoryDraft(document)),
    );
    // await 后页面可能已销毁，先检查 mounted，才能安全继续处理界面。
    if (!mounted || result == null || !result.ok) return;
    if (result.previewSceneId != null) {
      controller.replayScene(result.previewSceneId!);
    }
  }

  void _message(PlayerResult result, String success) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(result.ok ? success : result.error!.message)),
    );
  }

  @override
  Widget build(
    BuildContext context,
  ) => ValueListenableBuilder<PlaybackSnapshot>(
    // 订阅的是低频界面快照；舞台的逐帧人物运动走 Game.update，不靠 build 实现。
    valueListenable: controller.playback,
    builder: (context, state, _) {
      final loaded = controller.manifest != null;
      return Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  controller.document?.title ?? '准备演出',
                  style: const TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              Text(
                state.revision == null ? '' : '版本 ${state.revision}',
                style: const TextStyle(color: Color(0xff687462)),
              ),
            ],
          ),
          const SizedBox(height: 12),
          RepaintBoundary(
            key: const ValueKey('story_stage'),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(12),
              child: AspectRatio(
                aspectRatio: 16 / 9,
                child: Stack(
                  fit: StackFit.expand,
                  children: [
                    ColoredBox(
                      color: const Color(0xff7d9b61),
                      child: ClipRect(
                        child: GameWidget(
                          key: ObjectKey(_game),
                          game: _game,
                          loadingBuilder: (_) =>
                              const Center(child: Text('正在加载像素素材…')),
                          errorBuilder: (_, e) =>
                              const Center(child: Text('素材加载失败，请检查资源包')),
                        ),
                      ),
                    ),
                    // 字幕叠在引擎上方，用 Flutter 文字布局保持中文测量与显示一致。
                    if (controller.sceneState?.subtitle != null)
                      _SubtitleOverlay(controller: controller),
                    if (state.status == PlaybackStatus.loading)
                      const ColoredBox(
                        color: Color(0x8871875e),
                        child: Center(child: CircularProgressIndicator()),
                      ),
                  ],
                ),
              ),
            ),
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                child: Text(
                  controller.sceneState?.scene.name ?? '等待剧情输入',
                  style: const TextStyle(color: Color(0xff52614d)),
                ),
              ),
              Text(
                '${(state.positionMs / 1000).toStringAsFixed(1)} / ${(state.durationMs / 1000).toStringAsFixed(1)} 秒',
              ),
            ],
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              FilledButton.icon(
                key: const ValueKey('play_pause'),
                onPressed: loaded
                    ? () {
                        state.status == PlaybackStatus.playing
                            ? controller.pause()
                            : controller.play();
                      }
                    : null,
                icon: Icon(
                  state.status == PlaybackStatus.playing
                      ? Icons.pause
                      : Icons.play_arrow,
                ),
                label: Text(
                  state.status == PlaybackStatus.playing ? '暂停' : '播放',
                ),
              ),
              OutlinedButton.icon(
                onPressed: loaded
                    ? () {
                        controller.seek(Duration.zero);
                        controller.play();
                      }
                    : null,
                icon: const Icon(Icons.replay),
                label: const Text('从头演出'),
              ),
              if (widget.enableEditing)
                OutlinedButton.icon(
                  key: const ValueKey('open_editor'),
                  onPressed: loaded && state.status != PlaybackStatus.loading
                      ? _edit
                      : null,
                  icon: const Icon(Icons.edit_outlined),
                  label: const Text('修改演出'),
                ),
              OutlinedButton.icon(
                key: const ValueKey('save_story'),
                onPressed: loaded
                    ? () async {
                        _message(await controller.save(), '已保存到本地');
                      }
                    : null,
                icon: const Icon(Icons.save_outlined),
                label: const Text('保存'),
              ),
              TextButton(
                onPressed: loaded
                    ? () async {
                        _message(
                          await controller.restorePrevious(),
                          '已恢复上一保存版本，点击播放查看',
                        );
                      }
                    : null,
                child: const Text('恢复上一保存'),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              if (loaded)
                for (final scene in controller.document!.scenes)
                  ActionChip(
                    label: Text(scene.name),
                    onPressed: () => controller.replayScene(scene.id),
                  ),
            ],
          ),
          const SizedBox(height: 12),
          Text(
            state.hasUnsavedChanges ? '● 有未保存修改' : '本地演出 · 点击修改，无需重新生成故事',
            style: TextStyle(
              fontSize: 12,
              color: state.hasUnsavedChanges
                  ? const Color(0xffa56e35)
                  : const Color(0xff687462),
            ),
          ),
        ],
      );
    },
  );
}

/// 字幕先在 320 宽的逻辑空间布局，再整体缩放，避免设备宽度改变分页结果。
class _SubtitleOverlay extends StatelessWidget {
  const _SubtitleOverlay({required this.controller});
  final StoryPlayerController controller;
  @override
  Widget build(BuildContext context) {
    final state = controller.sceneState!;
    final speaker = state.speakerId == null
        ? '旁白'
        : controller.document!.actors
              .firstWhere((a) => a.id == state.speakerId)
              .name;
    return LayoutBuilder(
      builder: (_, constraints) => Align(
        alignment: Alignment.bottomCenter,
        child: SizedBox(
          width: constraints.maxWidth,
          height: constraints.maxWidth / 320 * 52,
          child: FittedBox(
            fit: BoxFit.fill,
            child: Container(
              width: 320,
              height: 52,
              decoration: BoxDecoration(
                color: const Color(0xfff8ecc9),
                border: Border.all(color: const Color(0xff8a7150), width: 2),
              ),
              // 正文宽度 = 320 - 左右内边距 28 - 左右边框 4 = 288。
              // 与 FlutterTextLayout 同步修改，才能保证编译分页与显示一致。
              padding: const EdgeInsets.fromLTRB(14, 1, 14, 1),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    speaker,
                    maxLines: 1,
                    textScaler: TextScaler.noScaling,
                    style: const TextStyle(
                      fontFamily: 'StorySans',
                      fontSize: 8,
                      height: 1.1,
                      color: Color(0xff6b583e),
                    ),
                  ),
                  const SizedBox(height: 1),
                  Text(
                    state.subtitle!,
                    maxLines: 3,
                    textScaler: TextScaler.noScaling,
                    style: const TextStyle(
                      fontFamily: 'StorySans',
                      fontSize: 10,
                      height: 1.1,
                      color: Color(0xff3e4438),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
