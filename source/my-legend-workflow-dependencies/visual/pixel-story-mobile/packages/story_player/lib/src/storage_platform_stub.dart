import 'storage.dart';

// Web 预览保存仅在当前会话有效；它不是 Android 上的持久化文件实现。
StoryRepository createRepository() => MemoryStoryRepository();
