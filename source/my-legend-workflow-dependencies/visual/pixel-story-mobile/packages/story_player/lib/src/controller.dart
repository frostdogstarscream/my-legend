import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'compiler.dart';
import 'editing.dart';
import 'evaluator.dart';
import 'model.dart';
import 'resources.dart';
import 'storage.dart';

// 控制器拥有播放状态；界面按钮和引擎画面都读取它，不各自维护一份播放进度。
enum PlaybackStatus { idle, loading, playing, paused, ended, error, disposed }

/// 供 Flutter 界面订阅的轻量快照；它不是包含全部人物姿态的逐帧画面状态。
class PlaybackSnapshot {
  const PlaybackSnapshot({
    required this.status,
    this.positionMs = 0,
    this.durationMs = 0,
    this.documentId,
    this.revision,
    this.sceneId,
    this.blockId,
    this.hasUnsavedChanges = false,
  });
  final PlaybackStatus status;
  final double positionMs;
  final int durationMs;
  final String? documentId;
  final int? revision;
  final String? sceneId;
  final String? blockId;
  final bool hasUnsavedChanges;
}

/// 离散事件用于提示错误、记录场景切换等；持续的播放进度从 playback 读取。
class StoryPlayerEvent {
  const StoryPlayerEvent(
    this.kind, {
    this.documentId,
    this.revision,
    this.error,
  });
  final String kind;
  final String? documentId;
  final int? revision;
  final StoryError? error;
}

/// 异步操作的统一返回值：先检查 ok，再使用成功数据或显示 error。
/// savedRevision 指明实际落盘的版本，保存期间当前剧本可能已产生新版本。
class PlayerResult {
  const PlayerResult.success({
    required this.document,
    required this.manifest,
    this.changed = false,
    this.previewSceneId,
    this.savedRevision,
  }) : error = null;
  const PlayerResult.failure(this.error)
    : document = null,
      manifest = null,
      changed = false,
      previewSceneId = null,
      savedRevision = null;
  final StoryDocument? document;
  final StoryManifest? manifest;
  final StoryError? error;
  final bool changed;
  final String? previewSceneId;
  final int? savedRevision;
  bool get ok => error == null;
}

typedef LoadResult = PlayerResult;
typedef ApplyResult = PlayerResult;
typedef SaveResult = PlayerResult;

/// 可替换时钟：正式环境用 Stopwatch，测试用可手动推进的时钟，不需要真实等待。
abstract interface class PlaybackClock {
  double get elapsedMs;
  void start();
  void stop();
  void reset();
}

/// 单调计时不受系统日期或时区调整影响，适合用于动画播放进度。
class MonotonicPlaybackClock implements PlaybackClock {
  final Stopwatch _watch = Stopwatch();
  @override
  double get elapsedMs => _watch.elapsedMicroseconds / 1000;
  @override
  void start() => _watch.start();
  @override
  void stop() => _watch.stop();
  @override
  void reset() => _watch.reset();
}

/// 播放器的总协调者：加载、计时、计算画面、应用修改、保存和生命周期处理。
/// 对外只发布一份已编译版本；资源加载或新剧本编译失败时，不替换原来的剧本。
class StoryPlayerController {
  StoryPlayerController({
    StoryRepository? repository,
    TextLayoutService? layout,
    PlaybackClock? clock,
    Future<void> Function()? preload,
  }) : repository = repository ?? createDefaultRepository(),
       _clock = clock ?? MonotonicPlaybackClock() {
    _preload = preload ?? resources.preload;
    _compiler = StoryCompiler(layout: layout ?? FlutterTextLayout(resources));
  }
  // 依赖可注入，方便测试；Controller 负责释放自己创建的 PlayerResources。
  final StoryRepository repository;
  final PlayerResources resources = PlayerResources();
  final PlaybackClock _clock;
  late final Future<void> Function() _preload;
  late final StoryCompiler _compiler;
  // ValueNotifier 驱动界面刷新，广播 Stream 传递离散事件，二者用途不同。
  final _playback = ValueNotifier<PlaybackSnapshot>(
    const PlaybackSnapshot(status: PlaybackStatus.idle),
  );
  final _events = StreamController<StoryPlayerEvent>.broadcast();
  ValueListenable<PlaybackSnapshot> get playback => _playback;
  Stream<StoryPlayerEvent> get events => _events.stream;
  StoryManifest? get manifest => _manifest;
  SceneState? get sceneState => _sceneState;
  StoryDocument? get document => _manifest?.document;
  bool get isDisposed => _disposed;
  bool get canRender => _hostVisible && _foreground && !_disposed;

  // _manifest 是当前已生效的演出清单；_sceneState 是它在当前时刻的计算结果。
  StoryManifest? _manifest;
  SceneState? _sceneState;
  PlaybackStatus _status = PlaybackStatus.idle;
  // 暂停/跳转位置作为基准，播放时再加上“本次启动以来”的时钟增量。
  double _baseMs = 0;
  double _lastNotificationMs = -1000;
  double _lastEvaluatedMs = 0;
  String? _savedHash;
  int? _savedRevision;
  // 每次加载/编辑/恢复都会换操作编号；旧异步请求返回时不能覆盖新请求。
  int _operation = 0;
  bool _applying = false;
  bool _disposed = false;
  bool _hostVisible = true;
  bool _foreground = true;

  void _check() {
    if (_disposed) throw const StoryError('DISPOSED', '播放器已释放');
  }

  double get _position =>
      _baseMs + (_status == PlaybackStatus.playing ? _clock.elapsedMs : 0);
  void _event(String kind, [StoryError? error]) {
    if (!_disposed) {
      _events.add(
        StoryPlayerEvent(
          kind,
          documentId: document?.id,
          revision: document?.revision,
          error: error,
        ),
      );
    }
  }

  PlayerResult _failure(Object error) {
    final problem = error is StoryError
        ? error
        : const StoryError('ASSET_LOAD_FAILED', '操作失败，请检查素材与运行环境');
    if (_disposed) {
      return const PlayerResult.failure(StoryError('DISPOSED', '播放器已释放'));
    }
    _status = _manifest == null ? PlaybackStatus.error : PlaybackStatus.paused;
    _event('error', problem);
    _publish(force: true);
    return PlayerResult.failure(problem);
  }

  // 既比较内容也比较版本：恢复旧内容会产生新版本，仍需用户明确保存。
  bool get hasUnsavedChanges =>
      document != null &&
      (document!.contentHash != _savedHash ||
          document!.revision != _savedRevision);

  /// 先计算供引擎逐帧读取的 SceneState，再按需通知 Flutter 界面。
  void _publish({bool force = false}) {
    if (_disposed) return;
    final m = _manifest;
    final time = m == null
        ? 0.0
        : _position.clamp(0.0, m.durationMs.toDouble());
    _lastEvaluatedMs = time;
    final previous = _sceneState;
    if (m != null) _sceneState = const SceneEvaluator().evaluate(m, time);
    if (previous?.scene.id != _sceneState?.scene.id) _event('scene_changed');
    if (previous?.blockId != _sceneState?.blockId) _event('block_changed');
    // 普通进度最多约每 100ms 通知一次；场景/剧情块变化及强制刷新立即通知。
    // 仅限制界面快照通知频率，不限制上面的逐帧人物姿态计算。
    if (force ||
        time - _lastNotificationMs >= 100 ||
        previous?.blockId != _sceneState?.blockId ||
        previous?.scene.id != _sceneState?.scene.id) {
      _lastNotificationMs = time;
      _playback.value = PlaybackSnapshot(
        status: _status,
        positionMs: time,
        durationMs: m?.durationMs ?? 0,
        documentId: document?.id,
        revision: document?.revision,
        sceneId: _sceneState?.scene.id,
        blockId: _sceneState?.blockId,
        hasUnsavedChanges: hasUnsavedChanges,
      );
    }
  }

  /// 加载流程：暂停 → 输入/版本校验 → 素材就绪 → 完整编译 → 发布新版本。
  Future<LoadResult> load(
    StoryDocument input, {
    bool discardUnsaved = false,
  }) async {
    _check();
    pause();
    final token = ++_operation;
    _status = PlaybackStatus.loading;
    _publish(force: true);
    try {
      final normalized = StoryDocument.fromJson(input.toJson());
      final old = document;
      if (old != null &&
          !discardUnsaved &&
          hasUnsavedChanges &&
          normalized.contentHash != old.contentHash) {
        throw const StoryError('REVISION_CONFLICT', '当前修改尚未保存，请先保存或确认放弃');
      }
      if (old != null &&
          old.id == normalized.id &&
          (old.sourceVersion != normalized.sourceVersion ||
              normalized.revision < old.revision ||
              (normalized.revision == old.revision &&
                  normalized.contentHash != old.contentHash))) {
        throw const StoryError('REVISION_CONFLICT', '输入修订号或源版本冲突');
      }
      // 字体必须先加载，才能用实际字形测量字幕分页；等待期间可能出现新操作。
      await _preload();
      if (_disposed || token != _operation) {
        return const PlayerResult.failure(
          StoryError('REVISION_CONFLICT', '加载已被更新操作替代'),
        );
      }
      // 完整编译成功后才替换 _manifest，失败不会留下半新半旧的画面数据。
      final compiled = _compiler.compile(normalized);
      _manifest = compiled;
      _baseMs = 0;
      _clock.stop();
      _clock.reset();
      _status = PlaybackStatus.paused;
      _lastNotificationMs = -1000;
      // 宿主输入被视为未修改的基准；这不表示已写入本地文件。
      // 后续编辑会变成未保存状态，直到用户明确调用 save。
      _savedHash = normalized.contentHash;
      _savedRevision = normalized.revision;
      _publish(force: true);
      _event('loaded');
      return PlayerResult.success(document: document!, manifest: compiled);
    } catch (e) {
      if (token != _operation || _disposed) {
        return const PlayerResult.failure(
          StoryError('REVISION_CONFLICT', '加载已失效'),
        );
      }
      return _failure(e);
    }
  }

  /// 从当前基准位置开始计时；已播完时再次播放会从全剧开头开始。
  void play() {
    _check();
    if (_manifest == null || _status == PlaybackStatus.loading || _applying) {
      _event('error', const StoryError('NOT_READY', '素材或剧情尚未就绪'));
      return;
    }
    if (!canRender || _status == PlaybackStatus.playing) return;
    if (_status == PlaybackStatus.ended) _baseMs = 0;
    _clock.stop();
    _clock.reset();
    _clock.start();
    _status = PlaybackStatus.playing;
    _publish(force: true);
    _event('playback_status_changed');
  }

  /// 先捕获当前位置，再停表并清零，保证下次播放不重复累计旧时间。
  void pause() {
    _check();
    if (_status == PlaybackStatus.playing) {
      _baseMs = _position;
      _clock.stop();
      _clock.reset();
      _status = PlaybackStatus.paused;
      _publish(force: true);
      _event('playback_status_changed');
    }
  }

  /// 跳转会暂停播放并立即重新计算画面，不需要把之前的动画逐帧播放一遍。
  void seek(Duration position) {
    _check();
    if (_manifest == null) return;
    pause();
    _baseMs = (position.inMicroseconds / 1000).clamp(
      0.0,
      _manifest!.durationMs.toDouble(),
    );
    _status = _baseMs >= _manifest!.durationMs
        ? PlaybackStatus.ended
        : PlaybackStatus.paused;
    _publish(force: true);
  }

  /// 编译清单中保存的是全剧时间，重播场景只需跳到它的 startMs 后播放。
  void replayScene(String sceneId) {
    _check();
    final m = _manifest;
    if (m == null) return;
    final scene = m.scenes.firstWhere(
      (s) => s.scene.id == sceneId,
      orElse: () => throw const StoryError('INVALID_REFERENCE', '场景不存在'),
    );
    seek(Duration(milliseconds: scene.startMs));
    play();
  }

  /// 宿主页签不可见时暂停；重新可见不自动续播，等待用户点击播放。
  void setHostVisible(bool visible) {
    _check();
    if (!visible) pause();
    _hostVisible = visible;
    _publish(force: true);
  }

  /// APP 进入后台等非 resumed 状态时停表，后台停留时间不会算入演出进度。
  void setAppLifecycle(AppLifecycleState state) {
    if (_disposed) return;
    if (state != AppLifecycleState.resumed) pause();
    _foreground = state == AppLifecycleState.resumed;
    _publish(force: true);
  }

  /// 由引擎每帧调用。dt 仅用于发现长帧卡顿，实际进度读取单调时钟而非累加 dt。
  void onFrame(double dt) {
    if (_disposed || _status != PlaybackStatus.playing) return;
    if (dt > .25) {
      _clock.stop();
      _clock.reset();
      // 长帧后退回最近已计算的画面时刻，避免恢复时直接跳过一段对白。
      // 不使用低频通知的 PlaybackSnapshot，它可能比上一帧更旧。
      _baseMs = _lastEvaluatedMs;
      _status = PlaybackStatus.paused;
      _event('error', const StoryError('FRAME_INTERRUPTED', '演出暂时卡顿，点击播放继续'));
      _publish(force: true);
      return;
    }
    if (_position >= _manifest!.durationMs) {
      _clock.stop();
      _clock.reset();
      _baseMs = _manifest!.durationMs.toDouble();
      _status = PlaybackStatus.ended;
      _event('playback_status_changed');
      _publish(force: true);
    } else {
      _publish();
    }
  }

  /// 编辑事务：暂停 → 校验草稿 → 重编译全剧 → 发布版本 → 定位受影响场景。
  /// 此处发布后保持暂停；编辑面板关闭后由 View 决定是否立即重播。
  Future<ApplyResult> applyDraft(StoryDraft draft) async {
    _check();
    if (document == null || _applying || _status == PlaybackStatus.loading) {
      return const PlayerResult.failure(StoryError('NOT_READY', '正在处理内容，请稍后'));
    }
    pause();
    _applying = true;
    final token = ++_operation;
    try {
      final edit = const EditService().apply(document!, draft);
      if (!edit.changed) {
        return PlayerResult.success(
          document: document!,
          manifest: _manifest!,
          previewSceneId: edit.sceneId,
        );
      }
      await _preload();
      if (_disposed || token != _operation) {
        return const PlayerResult.failure(
          StoryError('REVISION_CONFLICT', '编辑已失效'),
        );
      }
      // MVP 剧本很短，完整重编译比维护局部缓存简单，也能重新检查所有时序约束。
      // 编译成功以前，当前播放清单保持不变。
      final compiled = _compiler.compile(edit.document);
      _manifest = compiled;
      _baseMs = compiled.scenes
          .firstWhere((s) => s.scene.id == edit.sceneId)
          .startMs
          .toDouble();
      _clock.stop();
      _clock.reset();
      _status = PlaybackStatus.paused;
      _lastNotificationMs = -1000;
      _publish(force: true);
      _event('revision_applied');
      return PlayerResult.success(
        document: document!,
        manifest: compiled,
        changed: true,
        previewSceneId: edit.sceneId,
      );
    } catch (e) {
      if (token != _operation || _disposed) {
        return const PlayerResult.failure(
          StoryError('REVISION_CONFLICT', '编辑已失效'),
        );
      }
      return _failure(e);
    } finally {
      _applying = false;
    }
  }

  /// 保存的是调用瞬间捕获的不可变版本，而不是 await 结束时恰好正在显示的版本。
  Future<SaveResult> save() async {
    _check();
    final captured = document, capturedManifest = _manifest;
    if (captured == null) {
      return const PlayerResult.failure(StoryError('NOT_READY', '尚无剧情'));
    }
    try {
      await repository.save(captured);
      if (!_disposed && document?.id == captured.id) {
        // 若等待写盘时又发生编辑，当前文档与 captured 不同，仍会显示“未保存”。
        _savedHash = captured.contentHash;
        _savedRevision = captured.revision;
        _publish(force: true);
        _event('saved');
      }
      return PlayerResult.success(
        document: captured,
        manifest: capturedManifest!,
        savedRevision: captured.revision,
      );
    } catch (e) {
      if (_disposed) {
        return const PlayerResult.failure(StoryError('DISPOSED', '播放器已释放'));
      }
      _event('error', const StoryError('SAVE_FAILED', '本地保存失败，修改尚未落盘'));
      return const PlayerResult.failure(
        StoryError('SAVE_FAILED', '本地保存失败，修改尚未落盘'),
      );
    }
  }

  /// 恢复上一个已保存内容，但生成当前 revision + 1；不会把版本号退回过去。
  /// 恢复操作不自动落盘，用户可以先预览，再决定是否保存。
  Future<ApplyResult> restorePrevious() async {
    _check();
    pause();
    final old = document;
    if (old == null) {
      return const PlayerResult.failure(StoryError('NOT_READY', '尚无剧情'));
    }
    final token = ++_operation;
    try {
      final restored = await repository.read(old.id, previous: true);
      if (_disposed || token != _operation) {
        return const PlayerResult.failure(
          StoryError('REVISION_CONFLICT', '恢复已失效'),
        );
      }
      if (restored == null) {
        return const PlayerResult.failure(
          StoryError('NO_PREVIOUS_VERSION', '没有上一个已保存版本'),
        );
      }
      await _preload();
      if (_disposed || token != _operation) {
        return const PlayerResult.failure(
          StoryError('REVISION_CONFLICT', '恢复已失效'),
        );
      }
      final compiled = _compiler.compile(
        restored.withRevision(old.revision + 1),
      );
      _manifest = compiled;
      _baseMs = 0;
      _clock.stop();
      _clock.reset();
      _status = PlaybackStatus.paused;
      _publish(force: true);
      _event('revision_applied');
      return PlayerResult.success(
        document: document!,
        manifest: compiled,
        changed: true,
        previewSceneId: document!.scenes.first.id,
      );
    } catch (e) {
      if (_disposed || token != _operation) {
        return const PlayerResult.failure(
          StoryError('REVISION_CONFLICT', '恢复已失效'),
        );
      }
      return _failure(e);
    }
  }

  /// 导出结构化剧情，供宿主接入；不是导出 MP4 或屏幕录制。
  StoryDocument exportDocument() {
    _check();
    return document ?? (throw const StoryError('NOT_READY', '尚无剧情'));
  }

  /// 宿主负责调用：使未完成的异步操作失效，然后释放时钟、订阅通知与图像资源。
  Future<void> dispose() async {
    if (_disposed) return;
    _operation++;
    _clock.stop();
    _status = PlaybackStatus.disposed;
    _playback.value = const PlaybackSnapshot(status: PlaybackStatus.disposed);
    _disposed = true;
    await _events.close();
    _playback.dispose();
    resources.dispose();
  }
}
