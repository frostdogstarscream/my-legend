import 'dart:convert';
import 'dart:io';
import 'package:path_provider/path_provider.dart';
import 'model.dart';
import 'storage.dart';

StoryRepository createRepository() => FileStoryRepository();

/// IO 平台的文件仓库：每个文档目录只维护 current.json 和 previous.json。
/// root 可注入临时目录，方便在开发机测试，不依赖手机上的真实路径。
class FileStoryRepository implements StoryRepository {
  FileStoryRepository({Future<Directory> Function()? root})
    : _root = root ?? getApplicationSupportDirectory;
  final Future<Directory> Function() _root;
  // Future 链作为串行写队列，防止两次保存交错覆盖 current/previous。
  Future<void> _tail = Future<void>.value();

  Future<Directory> _directory(String id) async {
    // 先校验 ID，再组成路径；限制为单段安全名称，不能包含 ../ 等路径内容。
    identifier(id, 'document_id');
    final parent = Directory('${(await _root()).path}/story_player');
    await parent.create(recursive: true);
    final child = Directory('${parent.path}/$id');
    if (await FileSystemEntity.type(child.path, followLinks: false) ==
        FileSystemEntityType.link) {
      throw const StoryError('SAVE_FAILED', '存储目录不能是符号链接');
    }
    if (!await child.exists()) {
      var count = 0;
      await for (final entry in parent.list(followLinks: false)) {
        if (entry is Directory) count++;
      }
      if (count >= 20) throw const StoryError('CAPACITY_EXCEEDED', '最多保存20份故事');
      await child.create();
    }
    return child;
  }

  Future<void> _atomicWrite(File target, String value) async {
    final temporary = File('${target.path}.tmp');
    if (await FileSystemEntity.type(target.path, followLinks: false) ==
            FileSystemEntityType.link ||
        await FileSystemEntity.type(temporary.path, followLinks: false) ==
            FileSystemEntityType.link) {
      throw const StoryError('SAVE_FAILED', '存储文件不能是符号链接');
    }
    // 先完整写临时文件，再替换目标，减少中断后留下半份 JSON 的风险。
    // 这是单文件替换，不代表 current 和 previous 构成跨文件数据库事务。
    await temporary.writeAsString(value, flush: true);
    await temporary.rename(target.path);
  }

  // 额外记录内容哈希用于发现损坏，不是加密或防恶意篡改的签名。
  JsonMap _envelope(StoryDocument document) => {
    'document': document.toJson(),
    'content_hash': document.contentHash,
  };

  Future<StoryDocument?> _readFile(File file) async {
    if (!await file.exists()) return null;
    if (await file.length() > 1024 * 1024 ||
        await FileSystemEntity.type(file.path, followLinks: false) ==
            FileSystemEntityType.link) {
      throw const StoryError('CORRUPT_LOCAL_DATA', '保存文件异常');
    }
    try {
      final envelope = object(jsonDecode(await file.readAsString()), 'saved');
      // 从磁盘读回也要经过输入协议校验，不能直接信任已保存的数据。
      final document = StoryDocument.fromJson(
        object(envelope['document'], 'document'),
      );
      if (document.contentHash != envelope['content_hash']) {
        throw const StoryError('CORRUPT_LOCAL_DATA', '保存内容校验失败');
      }
      return document;
    } on StoryError {
      rethrow;
    } on FormatException {
      throw const StoryError('CORRUPT_LOCAL_DATA', '保存文件格式损坏');
    }
  }

  @override
  Future<void> save(StoryDocument document) {
    final next = _tail.then((_) async {
      try {
        final directory = await _directory(document.id);
        final current = File('${directory.path}/current.json');
        final old = await _readFile(current);
        if (old != null && old.contentHash != document.contentHash) {
          await _atomicWrite(
            File('${directory.path}/previous.json'),
            jsonEncode(_envelope(old)),
          );
        }
        await _atomicWrite(current, jsonEncode(_envelope(document)));
      } on StoryError {
        rethrow;
      } on FileSystemException {
        throw const StoryError('SAVE_FAILED', '本地保存失败，修改尚未落盘');
      }
    });
    // 返回原 Future，让本次调用者看到错误；队列自身消化错误，后续保存仍可执行。
    _tail = next.catchError((Object _) {});
    return next;
  }

  @override
  Future<StoryDocument?> read(String id, {bool previous = false}) async {
    // 先等已有保存完成，避免在一次写操作中途读取。
    await _tail;
    final directory = await _directory(id);
    final file = File(
      '${directory.path}/${previous ? 'previous' : 'current'}.json',
    );
    try {
      final result = await _readFile(file);
      if (result != null && result.id != id) {
        throw const StoryError('CORRUPT_LOCAL_DATA', '文档ID不匹配');
      }
      if (result == null && !previous) return await read(id, previous: true);
      return result;
    } on StoryError {
      // 当前文件损坏时尝试读上一份有效备份；这里只回退读取，不修复损坏文件。
      if (!previous) {
        final backup = await read(id, previous: true);
        if (backup != null) return backup;
      }
      rethrow;
    } on FileSystemException {
      throw const StoryError('CORRUPT_LOCAL_DATA', '无法读取本地内容');
    }
  }
}
