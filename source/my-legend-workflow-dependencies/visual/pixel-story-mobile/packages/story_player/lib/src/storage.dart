import 'model.dart';
// 条件导入：手机/桌面等 IO 平台用文件保存；Web 预览使用内存实现。
import 'storage_platform_stub.dart'
    if (dart.library.io) 'storage_platform_io.dart'
    as platform;

/// 存储接口与播放器解耦；需要换成宿主自己的存储时，只需提供此接口的实现。
/// 保存的是结构化剧本，不包含逐帧画面、图集文件或视频文件。
abstract interface class StoryRepository {
  Future<void> save(StoryDocument document);
  Future<StoryDocument?> read(String id, {bool previous = false});
}

StoryRepository createDefaultRepository() => platform.createRepository();

/// 测试和 Web 预览用的会话内存仓库；刷新页面后不能保证保留内容。
/// 文档不可变，因此保存对象引用不会被后续草稿编辑污染。
class MemoryStoryRepository implements StoryRepository {
  final Map<String, StoryDocument> _current = {};
  final Map<String, StoryDocument> _previous = {};
  @override
  Future<void> save(StoryDocument document) async {
    final old = _current[document.id];
    // 只保留一个上版本；仅修订号变化而内容相同，不占用上一版本备份。
    if (old != null && old.contentHash != document.contentHash) {
      _previous[document.id] = old;
    }
    _current[document.id] = document;
  }

  @override
  Future<StoryDocument?> read(String id, {bool previous = false}) async =>
      (previous ? _previous : _current)[id];
}
