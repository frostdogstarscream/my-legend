import 'dart:ui' as ui;
import 'package:characters/characters.dart';
import 'package:crypto/crypto.dart';
import 'package:flutter/painting.dart';
import 'package:flutter/services.dart';
import 'compiler.dart';
import 'model.dart';

// 组件包自己的资源前缀，不是演示 APP 的 assets/fixtures 输入目录。
const assetPrefix = 'packages/story_player/assets/';

/// 每个控制器持有自己的图集资源，供编译器和 Flame 共享一次加载结果。
class PlayerResources {
  ui.Image? atlas;
  String fontHash = '';
  Future<void>? _loading;
  bool _disposed = false;
  // 缓存进行中的 Future，合并并发预加载；失败后清空，允许下一次重试。
  Future<void> preload() => _loading ??= _load().catchError((Object e) {
    _loading = null;
    throw e;
  });
  Future<void> _load() async {
    final font = await rootBundle.load('${assetPrefix}fonts/StorySansSC.ttf');
    // 记录实际字体文件哈希，让字幕布局签名能区分不同字体版本。
    final hash = sha256
        .convert(
          font.buffer.asUint8List(font.offsetInBytes, font.lengthInBytes),
        )
        .toString();
    final loader = FontLoader('StorySans')..addFont(Future.value(font));
    await loader.load();
    final bytes = await rootBundle.load(
      '${assetPrefix}basic_pixel/1.0.0/actors.png',
    );
    final codec = await ui.instantiateImageCodec(
      bytes.buffer.asUint8List(bytes.offsetInBytes, bytes.lengthInBytes),
    );
    final frame = await codec.getNextFrame();
    codec.dispose();
    // await 期间控制器可能已被释放；新解码的图像不能泄漏，也不能再发布。
    if (_disposed) {
      frame.image.dispose();
      throw const StoryError('DISPOSED', '素材加载已取消');
    }
    atlas = frame.image;
    fontHash = hash;
  }

  // 图像属于当前播放器，按实例释放；不清空其他播放器可能使用的全局缓存。
  // FontLoader 注册的字体由 Flutter 管理，不随 atlas 一起销毁。
  void dispose() {
    _disposed = true;
    atlas?.dispose();
    atlas = null;
  }
}

/// 正式字幕分页器：使用已加载字体测量宽度，而非按汉字数量猜测换行。
/// 这里的 288 宽、10 字号、1.1 行高、3 行必须与 player_view 的字幕区一致。
class FlutterTextLayout implements TextLayoutService {
  FlutterTextLayout(this.resources);
  final PlayerResources resources;
  @override
  String get signature => 'StorySans:${resources.fontHash}:288:10:1.1:3';
  @override
  List<String> pages(String text) {
    final result = <String>[];
    var current = '';
    final painter = TextPainter(
      textDirection: TextDirection.ltr,
      maxLines: 3,
      textScaler: TextScaler.noScaling,
    );
    // 逐个添加可见字符，超过三行就结束当前页；每个原始字符都保留在某一页中。
    for (final char in text.characters) {
      final candidate = '$current$char';
      painter.text = TextSpan(
        text: candidate,
        style: const TextStyle(
          fontFamily: 'StorySans',
          fontSize: 10,
          height: 1.1,
        ),
      );
      painter.layout(maxWidth: 288);
      if (painter.didExceedMaxLines && current.isNotEmpty) {
        result.add(current);
        current = char;
      } else {
        current = candidate;
      }
    }
    painter.dispose();
    if (current.isNotEmpty) result.add(current);
    return result;
  }
}
