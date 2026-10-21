import 'dart:ui' as ui;
import 'package:flame/components.dart';
import 'package:flame/game.dart';
import 'package:flutter/painting.dart';
import 'compiler.dart';
import 'controller.dart';
import 'evaluator.dart';
import 'model.dart';

/// 画面层只做两件事：每帧推动控制器更新，再把 SceneState 映射到绘图组件。
/// 不在引擎内重新解释剧情、估算字幕时长或维护第二套播放时间。
class StoryFlameGame extends FlameGame {
  StoryFlameGame(this.controller)
    : super(
        camera: CameraComponent.withFixedResolution(width: 320, height: 180),
      ) {
    // 后台暂停交由 View + Controller 统一处理，避免引擎停了但时钟还在走。
    pauseWhenBackgrounded = false;
    // 固定 320×180 逻辑舞台，原点在左上；手机尺寸变化由相机等比适配。
    camera.viewfinder.anchor = Anchor.topLeft;
    camera.viewfinder.position = Vector2.zero();
  }
  final StoryPlayerController controller;
  final _background = _StoryBackground();
  final _actors = <_PixelActor>[];
  final _sprites = <Sprite>[];
  final _fade = _FadeLayer();
  SceneState? _lastState;

  @override
  Future<void> onLoad() async {
    await controller.resources.preload();
    final image = controller.resources.atlas!;
    // 共享一张图集：4 模板 × 4 颜色 × 3 配饰 × 4 朝向 × 2 步态 = 384 帧。
    // 图集中每帧 32×32，每行 16 帧；这里只切出 Sprite 引用，不重复解码图片。
    for (var i = 0; i < 384; i++) {
      _sprites.add(
        Sprite(
          image,
          srcPosition: Vector2((i % 16) * 32.0, (i ~/ 16) * 32.0),
          srcSize: Vector2.all(32),
        ),
      );
    }
    await world.add(_background);
    // 每场最多四人，提前创建四个组件复用；它们并不永久绑定某个角色 ID。
    for (var i = 0; i < 4; i++) {
      // 挂载 SpriteComponent 时必须已有精灵；pose 为空，所以占位帧不会显示。
      final actor = _PixelActor(_sprites.first);
      _actors.add(actor);
      await world.add(actor);
    }
    await world.add(_fade);
  }

  @override
  void update(double dt) {
    // 顺序很重要：先计算最新画面，再读取状态，避免始终显示上一帧的结果。
    controller.onFrame(dt);
    final state = controller.sceneState;
    if (state != null &&
        (!identical(state, _lastState) ||
            _actors.any((a) => a.actor == null))) {
      _lastState = state;
      _background.kind = state.scene.background;
      final m = controller.manifest!;
      for (var i = 0; i < _actors.length; i++) {
        if (i >= state.actors.length) {
          _actors[i].pose = null;
          continue;
        }
        final pose = state.actors[i];
        final actor = m.document.actors.firstWhere((a) => a.id == pose.roleId);
        final color = ['blue', 'green', 'red', 'brown'].indexOf(actor.color);
        final accessory = ['none', 'hat', 'beard'].indexOf(actor.accessory);
        // 将模板/颜色/配饰/朝向/步态编码为一维索引，顺序要与素材生成器一致。
        final index =
            ((actor.template.index * 4 + color) * 3 + accessory) * 8 +
            pose.facing.index * 2 +
            pose.walkFrame;
        _actors[i].sync(actor, pose, _sprites[index], i);
      }
    }
    // 暂停时关闭转场遮罩，让用户能看清正在编辑的场景。
    _fade.opacity = controller.playback.value.status == PlaybackStatus.playing
        ? state?.fadeOpacity ?? 0
        : 0;
    super.update(dt);
  }

  @override
  void onRemove() {
    // 此处只释放组件自己的文字布局；图像由 Controller 管理，不能重复释放。
    for (final actor in _actors) {
      actor.disposeText();
    }
    super.onRemove();
  }
}

/// 单个人物的绘图组件：精灵、名字牌和简易情绪气泡，不承担剧情逻辑。
class _PixelActor extends SpriteComponent {
  _PixelActor(Sprite placeholder)
    : super(
        sprite: placeholder,
        size: Vector2.all(32),
        // 32×32 帧内的脚下点为 (16,30)，使 position 与模板脚下坐标一致。
        anchor: const Anchor(.5, .9375),
      ) {
    // 不平滑像素边缘，放大后仍保持像素风格。
    paint.filterQuality = ui.FilterQuality.none;
  }
  StoryActor? actor;
  ActorPose? pose;
  TextPainter? _name;
  void sync(StoryActor model, ActorPose value, Sprite frame, int order) {
    // 仅当名字变化时重建文字布局，避免每帧重复测量。
    if (actor?.name != model.name) {
      _name?.dispose();
      _name = TextPainter(
        text: TextSpan(
          text: model.name,
          style: const TextStyle(
            fontFamily: 'StorySans',
            fontSize: 7,
            color: Color(0xfffcf1d5),
          ),
        ),
        textDirection: TextDirection.ltr,
        maxLines: 1,
        ellipsis: '…',
      )..layout(maxWidth: 78);
    }
    actor = model;
    pose = value;
    sprite = frame;
    position.setValues(
      value.position.x + value.offsetX,
      value.position.y + value.offsetY,
    );
    // Y 越大越靠前绘制；使用真实站位而非点头偏移，避免动作期间遮挡顺序抖动。
    priority = (value.position.y * 100).round() + order;
  }

  @override
  void render(ui.Canvas canvas) {
    if (pose == null || !pose!.visible) return;
    super.render(canvas);
    final label = _name;
    if (label != null) {
      final x = 16 - label.width / 2;
      canvas.drawRect(
        ui.Rect.fromLTWH(x - 2, -10, label.width + 4, 10),
        ui.Paint()..color = const Color(0xcc3d513b),
      );
      label.paint(canvas, ui.Offset(x, -9));
    }
    // 表情由矩形绘制成简单气泡，首版不需要额外的逐表情图片素材。
    if (pose!.speaking || pose!.reaction != null) {
      final color = switch (pose!.reaction) {
        Emotion.angry => const Color(0xffca6d51),
        Emotion.sad => const Color(0xff91b6d3),
        Emotion.happy => const Color(0xffb5ce7d),
        Emotion.serious => const Color(0xffc2b5a0),
        _ => const Color(0xffecd591),
      };
      final pen = ui.Paint()..color = color;
      canvas.drawRect(const ui.Rect.fromLTWH(28, -1, 8, 8), pen);
      canvas.drawRect(const ui.Rect.fromLTWH(28, 6, 2, 3), pen);
      final ink = ui.Paint()..color = const Color(0xff514934);
      if (pose!.speaking && pose!.reaction == null) {
        for (var i = 0; i < 3; i++) {
          canvas.drawRect(ui.Rect.fromLTWH(29 + i * 2.0, 3, 1, 1), ink);
        }
      } else if (pose!.reaction == Emotion.surprised) {
        canvas.drawRect(const ui.Rect.fromLTWH(31, 0, 2, 4), ink);
        canvas.drawRect(const ui.Rect.fromLTWH(31, 5, 2, 1), ink);
      } else {
        canvas.drawRect(const ui.Rect.fromLTWH(29, 1, 1, 1), ink);
        canvas.drawRect(const ui.Rect.fromLTWH(33, 1, 1, 1), ink);
        final happy = pose!.reaction == Emotion.happy;
        final sad =
            pose!.reaction == Emotion.sad || pose!.reaction == Emotion.angry;
        canvas.drawRect(ui.Rect.fromLTWH(30, happy ? 5 : 4, 3, 1), ink);
        if (happy || sad) {
          canvas.drawRect(ui.Rect.fromLTWH(29, happy ? 4 : 5, 1, 1), ink);
          canvas.drawRect(ui.Rect.fromLTWH(33, happy ? 4 : 5, 1, 1), ink);
        }
      }
    }
  }

  void disposeText() {
    _name?.dispose();
    _name = null;
  }
}

/// 简易背景模板直接用 Canvas 绘制；换故事只选背景类型，不硬编码故事名称。
/// 负绘制优先级确保背景位于人物后方。
class _StoryBackground extends PositionComponent {
  _StoryBackground() : super(size: Vector2(320, 180), priority: -10000);
  BackgroundKind kind = BackgroundKind.courtyard;
  @override
  void render(ui.Canvas canvas) {
    void rect(double x, double y, double w, double h, int color) =>
        canvas.drawRect(
          ui.Rect.fromLTWH(x, y, w, h),
          ui.Paint()..color = Color(color),
        );
    final indoor = kind == BackgroundKind.indoor;
    rect(0, 0, 320, 180, indoor ? 0xffc4aa77 : 0xff8aaa68);
    for (var y = 0; y < 180; y += 12) {
      for (var x = 0; x < 320; x += 16) {
        if ((x ~/ 16 + y ~/ 12) % 3 == 0) {
          rect(x + 3, y + 5, 3, 2, indoor ? 0xffb59a6a : 0xff7c9e5d);
        }
      }
    }
    if (indoor) {
      rect(0, 0, 320, 57, 0xff75644e);
      rect(0, 52, 320, 7, 0xff514635);
      rect(42, 10, 46, 32, 0xff314a47);
      rect(46, 14, 38, 24, 0xffa5c1b0);
      rect(158, 26, 96, 10, 0xff9d754d);
      rect(166, 36, 8, 16, 0xff71513b);
      rect(237, 36, 8, 16, 0xff71513b);
      rect(196, 16, 25, 10, 0xffeee1b7);
    } else if (kind == BackgroundKind.courtyard) {
      rect(28, 21, 138, 53, 0xffd5bc83);
      rect(46, 13, 102, 8, 0xff795e45);
      rect(34, 21, 126, 6, 0xff795e45);
      rect(52, 9, 90, 4, 0xff8a6b48);
      rect(78, 42, 30, 32, 0xff5e5540);
      rect(118, 37, 28, 19, 0xff6c785b);
      rect(0, 84, 320, 4, 0xffcbb98c);
      rect(0, 94, 320, 32, 0xffc9b58b);
      for (var x = 6; x < 320; x += 24) {
        rect(x.toDouble(), 115, 12, 2, 0xffbca478);
      }
    } else {
      rect(0, 94, 320, 32, 0xffd0bf91);
      rect(190, 28, 73, 26, 0xff80a9a0);
      rect(199, 22, 53, 40, 0xff80a9a0);
      rect(205, 30, 32, 2, 0xffaac5b5);
    }
    if (!indoor) {
      for (final x in [17.0, 284.0]) {
        rect(x + 8, 51, 7, 30, 0xff826342);
        rect(x, 22, 26, 35, 0xff557849);
        rect(x + 4, 14, 18, 42, 0xff648b50);
        rect(x + 8, 20, 9, 3, 0xff82a260);
      }
    }
  }
}

/// 高优先级的转场遮罩覆盖整个舞台；透明度由求值器算好后传入。
class _FadeLayer extends PositionComponent {
  _FadeLayer() : super(size: Vector2(320, 180), priority: 100000);
  double opacity = 0;
  @override
  void render(ui.Canvas canvas) {
    if (opacity > 0) {
      canvas.drawRect(
        const ui.Rect.fromLTWH(0, 0, 320, 180),
        ui.Paint()
          ..color = Color.fromARGB(
            (255 * opacity).round().clamp(0, 255),
            28,
            37,
            27,
          ),
      );
    }
  }
}
