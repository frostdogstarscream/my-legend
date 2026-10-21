// 对外统一入口：宿主只导入这个文件，不需要直接依赖 src 下的具体绘图实现。
// 核心链路：StoryDocument → StoryCompiler → SceneEvaluator → Controller/View。
library;

export 'src/model.dart';
export 'src/compiler.dart';
export 'src/evaluator.dart';
export 'src/editing.dart';
export 'src/controller.dart';
export 'src/storage.dart';
export 'src/player_view.dart';
