import 'dart:math' as math;
import 'model.dart';

/// 320×180 逻辑舞台中的二维点；坐标不直接等同于手机屏幕的物理像素。
class Point2 {
  const Point2(this.x, this.y);
  final double x;
  final double y;
  double distanceTo(Point2 other) =>
      math.sqrt(math.pow(x - other.x, 2) + math.pow(y - other.y, 2));
  Point2 lerp(Point2 to, double p) =>
      Point2(x + (to.x - x) * p, y + (to.y - y) * p);
  List<double> toJson() => [x, y];
}

// 模板站位使用人物脚下坐标；不同背景共用同一套站位，避免每个故事单独配路径。
const anchors = <String, Point2>{
  'slot_1': Point2(72, 108),
  'slot_2': Point2(128, 108),
  'slot_3': Point2(192, 116),
  'slot_4': Point2(248, 116),
  'entry': Point2(12, 108),
  'exit': Point2(308, 108),
};

// 用位移较大的轴决定四方向朝向；两点重合时保留传入的默认方向。
Facing toward(Point2 from, Point2 to, [Facing fallback = Facing.down]) {
  final dx = to.x - from.x, dy = to.y - from.y;
  if (dx == 0 && dy == 0) return fallback;
  return dx.abs() >= dy.abs()
      ? (dx > 0 ? Facing.right : Facing.left)
      : (dy > 0 ? Facing.down : Facing.up);
}

/// MVP 走预设折线路径：同轴直走，否则先横向再纵向；不是自由移动或 A* 寻路。
List<Point2> pathBetween(Point2 from, Point2 to) {
  if (from.x == to.x || from.y == to.y) return [from, to];
  return [from, Point2(to.x, from.y), to];
}

/// 按总路径长度计算已走距离，再找到所在路段，返回该点的位置和朝向。
/// 不能把每段简单均分时间，否则长短路段的移动速度会忽快忽慢。
(Point2, Facing) samplePath(List<Point2> path, double progress) {
  final lengths = List.generate(
    path.length - 1,
    (i) => path[i].distanceTo(path[i + 1]),
  );
  final length = lengths.fold<double>(0, (a, b) => a + b);
  if (length == 0) return (path.last, Facing.down);
  var remaining = progress.clamp(0.0, 1.0) * length;
  for (var i = 0; i < lengths.length; i++) {
    if (lengths[i] == 0) continue;
    if (remaining < lengths[i] || i == lengths.length - 1) {
      return (
        // 路段内采用线性插值：起点 + (终点 - 起点) × 本段进度。
        path[i].lerp(path[i + 1], (remaining / lengths[i]).clamp(0.0, 1.0)),
        toward(path[i], path[i + 1]),
      );
    }
    remaining -= lengths[i];
  }
  return (path.last, toward(path[path.length - 2], path.last));
}
