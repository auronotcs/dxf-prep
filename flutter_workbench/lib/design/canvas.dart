import 'dart:math' as math;
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'controller.dart';
import 'point_editor.dart';
import 'snapping.dart';
import 'guide_actions.dart';
import 'dimension_painter.dart';
import 'dimension_actions.dart';
import 'appearance_dialog.dart' show previewColor;

Path entityPath(Json e) {
  final path = Path();
  switch (e['kind']) {
    case 'line':
      final a = point(e['start']);
      final b = point(e['end']);
      path.moveTo(a.dx, a.dy);
      path.lineTo(b.dx, b.dy);
    case 'circle':
      path.addOval(
        Rect.fromCircle(
          center: point(e['center']),
          radius: number(e['radius']),
        ),
      );
    case 'rectangle':
      path.addRect(Rect.fromPoints(point(e['corner_a']), point(e['corner_b'])));
    case 'polyline':
      final pts = (e['points'] as List).map(point).toList();
      if (pts.isNotEmpty) {
        path.moveTo(pts.first.dx, pts.first.dy);
        for (final p in pts.skip(1)) {
          path.lineTo(p.dx, p.dy);
        }
        if (e['closed'] == true) path.close();
      }
    case 'text':
      final b = bounds(e['bounds']);
      if (b != null) path.addRect(b);
  }
  return path;
}

class DesignViewport extends ChangeNotifier {
  double scale = 8;
  Offset translation = const Offset(60, 460);
  Size size = Size.zero;
  bool grid = true, snap = false;
  bool guidesShown = true;
  double snapGridStep = .125;
  SnapIndex snapIndex = SnapIndex([]);
  SnapTarget? snapTarget;
  int gestureEpoch = 0;
  List<Json> guides = [];
  String? hoveredGuideId, draggedGuideId;
  String? guideOrientation;
  double? guidePosition;
  String tool = 'select';
  Offset? start, current;
  Json? dimensionStart;
  String? selectedDimensionId, hoveredDimensionId;
  Json? dimensionPreview;
  Offset cursor = Offset.zero;
  bool cursorInside = false;
  List<String> hoveredIds = [];
  Offset dragDelta = Offset.zero;
  String hint =
      'Drag selected objects to move · Shift selects · middle-drag pans';
  Offset world(Offset p) =>
      Offset((p.dx - translation.dx) / scale, (translation.dy - p.dy) / scale);
  Offset screen(Offset p) =>
      Offset(p.dx * scale + translation.dx, translation.dy - p.dy * scale);
  Offset snapped(Offset p) {
    return snapWorld(world(p));
  }

  Offset snapWorld(
    Offset q, {
    Set<String> exclude = const {},
    String? excludeGuideId,
  }) {
    snapTarget = null;
    if (!snap) return q;
    final radius = 10 / scale;
    snapTarget = snapIndex.nearest(q, radius, exclude: exclude);
    var distance = snapTarget == null
        ? double.infinity
        : (snapTarget!.position - q).distance;
    void candidate(Offset p, String label) {
      final d = (p - q).distance;
      if (d <= radius && d < distance) {
        snapTarget = SnapTarget(p, label);
        distance = d;
      }
    }

    if (guidesShown) {
      for (final guide in guides.where((g) => g['visible'] == true)) {
        if (excludeGuideId != null && guide['id'] == excludeGuideId) continue;
        final position = guide['orientation'] == 'horizontal'
            ? Offset(q.dx, number(guide['position']))
            : Offset(number(guide['position']), q.dy);
        candidate(position, 'Guide');
      }
    }
    candidate(
      Offset(
        (q.dx / snapGridStep).round() * snapGridStep,
        (q.dy / snapGridStep).round() * snapGridStep,
      ),
      'Grid',
    );
    return snapTarget?.position ?? q;
  }

  void fit(Rect rect) {
    snapTarget = null;
    if (size.isEmpty) return;
    final width = math.max(rect.width, 1.0),
        height = math.max(rect.height, 1.0);
    scale = math
        .min((size.width - 100) / width, (size.height - 100) / height)
        .clamp(.001, 10000);
    translation = Offset(
      size.width / 2 - rect.center.dx * scale,
      size.height / 2 + rect.center.dy * scale,
    );
    notifyListeners();
  }

  void zoom(double factor, Offset anchor) {
    snapTarget = null;
    final w = world(anchor);
    scale = (scale * factor).clamp(.001, 10000);
    translation = Offset(anchor.dx - w.dx * scale, anchor.dy + w.dy * scale);
    notifyListeners();
  }

  void changed() => notifyListeners();
  double zoomPercent(String units) =>
      scale / (units == 'mm' ? 96 / 25.4 : 96) * 100;
  void setTool(String value) {
    gestureEpoch++;
    snapTarget = null;
    tool = value;
    start = null;
    dimensionStart = null;
    selectedDimensionId = hoveredDimensionId = null;
    dimensionPreview = null;
    guideOrientation = null;
    draggedGuideId = hoveredGuideId = null;
    guidePosition = null;
    current = null;
    dragDelta = Offset.zero;
    hint = value == 'select'
        ? 'Drag selected objects to move · Shift selects · middle-drag pans'
        : value == 'pan'
        ? 'Drag to pan'
        : value == 'measure'
        ? 'Click two points to measure'
        : 'Click the first point, then the second point';
    notifyListeners();
  }
}

class DesignCanvas extends StatefulWidget {
  const DesignCanvas({
    super.key,
    required this.model,
    required this.view,
    required this.layer,
    required this.dark,
    this.onFocus,
    this.onContextMenu,
    this.pointEditor,
    this.onDimension,
    this.onCleave,
  });
  final DesignController model;
  final DesignViewport view;
  final String layer;
  final bool dark;
  final VoidCallback? onFocus;
  final ValueChanged<Offset>? onContextMenu;
  final PointEditor? pointEditor;
  final Future<void> Function(List<Json>)? onDimension, onCleave;
  @override
  State<DesignCanvas> createState() => _DesignCanvasState();
}

class _DesignCanvasState extends State<DesignCanvas> {
  bool panning = false, box = false;
  bool moving = false, dragging = false;
  bool pointGesture = false;
  bool guideGesture = false;
  Json? draggingDimension;
  double? guideStartPosition;
  Offset? down;
  Offset? moveAnchor;
  int downEpoch = 0;
  int downRevision = -1;
  double downScale = 1;
  Offset downTranslation = Offset.zero;
  final paths = <String, Path>{};
  int cachedRevision = -1;
  DesignController get m => widget.model;
  DesignViewport get v => widget.view;
  @override
  void reassemble() {
    super.reassemble();
    cachedRevision = -1;
    cancelGesture();
  }

  void cache() {
    if (cachedRevision == m.state['revision']) return;
    paths.clear();
    for (final e in m.entities) {
      paths[e['id']] = entityPath(e);
    }
    v.snapIndex = SnapIndex(m.entities.where(m.visible));
    v.snapTarget = null;
    cachedRevision = m.state['revision'] ?? -1;
  }

  bool get shift => HardwareKeyboard.instance.isShiftPressed;
  List<String> hitObjects(Offset p, {bool includeLocked = false}) {
    final w = v.world(p);
    final tolerance = 6 / v.scale;
    final hits = <String>[];
    for (final e
        in (includeLocked ? m.entities.where(m.visible) : m.selectable)
            .toList()
            .reversed) {
      final path = paths[e['id']]!;
      final b = bounds(e['bounds']);
      if (b == null || !b.inflate(tolerance).contains(w)) continue;
      bool found =
          (e['kind'] != 'line' &&
              (e['kind'] != 'polyline' || e['closed'] == true)) &&
          path.contains(w);
      if (!found) {
        for (final metric in path.computeMetrics()) {
          final steps = math.min(
            20000,
            math.max(2, (metric.length / tolerance).ceil()),
          );
          for (var i = 0; i <= steps; i++) {
            final t = metric.getTangentForOffset(metric.length * i / steps);
            if (t != null && (t.position - w).distance < tolerance) {
              found = true;
              break;
            }
          }
          if (found) break;
        }
      }
      if (found) {
        hits.add(e['id'] as String);
      }
    }
    return hits;
  }

  Set<String> hit(Offset p) {
    final hits = hitObjects(p);
    if (hits.isNotEmpty) {
      final e = m.entities.firstWhere((e) => e['id'] == hits.first);
      final group = e['metadata']?['group_id'];
      final compound = e['metadata']?['compound_id'];
      return m.selectable
          .where(
            (other) =>
                other['id'] == e['id'] ||
                (group != null && other['metadata']?['group_id'] == group) ||
                (compound != null &&
                    other['metadata']?['compound_id'] == compound),
          )
          .map((e) => e['id'] as String)
          .toSet();
    }
    return {};
  }

  void updatePointer(Offset location) {
    v.cursor = v.world(location);
    v.cursorInside = true;
    v.hoveredIds = hitObjects(location, includeLocked: true);
    final dimensionHover =
        draggingDimension == null &&
            !guideGesture &&
            !pointGesture &&
            !moving &&
            !panning &&
            !box &&
            v.tool == 'select' &&
            widget.pointEditor?.active != true
        ? (dimensionAt(location)?['id'] as String?)
        : null;
    if (dimensionHover != v.hoveredDimensionId) {
      v.hoveredDimensionId = dimensionHover;
      setState(() {});
    }
    final hover =
        dimensionHover == null &&
            draggingDimension == null &&
            !guideGesture &&
            !pointGesture &&
            !moving &&
            !panning &&
            !box &&
            v.tool == 'select' &&
            widget.pointEditor?.active != true
        ? (hitGuide(location)?['id'] as String?)
        : null;
    if (hover != v.hoveredGuideId) {
      v.hoveredGuideId = hover;
      setState(() {});
    }
    if (panning || box) v.snapTarget = null;
    if (!pointGesture && !moving && !panning && !box && !guideGesture) {
      if (!{'select', 'pan'}.contains(v.tool) ||
          widget.pointEditor?.inserting == true) {
        final editorId = widget.pointEditor?.entityId;
        final q = v.snapWorld(
          v.world(location),
          exclude: editorId == null ? {} : {editorId},
        );
        if (v.start != null) v.current = q;
      } else {
        v.snapTarget = null;
      }
    }
    v.changed();
  }

  Offset snapEditedPoint(Offset q) {
    final id = widget.pointEditor?.entityId;
    return v.snapWorld(q, exclude: id == null ? {} : {id});
  }

  Json? dimensionAt(Offset p) {
    if (p.dx < 22 || p.dy < 22 || !(Offset.zero & v.size).contains(p)) {
      return null;
    }
    for (final d
        in (m.state['dimensions'] as List? ?? []).cast<Json>().reversed) {
      if (m.visible(d['annotation'] as Json) &&
          hitDimension(d, v.world(p), v.scale)) {
        return d;
      }
    }
    return null;
  }

  void dimensionMotion(Offset p) {
    final d = draggingDimension;
    if (d == null || down == null) return;
    final offset = draggedDimensionOffset(d, v.world(down!), v.world(p));
    v.dimensionPreview = previewDimensionDrag(d, v.world(down!), v.world(p));
    if (hasFreeDimensionLanding(d)) {
      v.hint = v.dimensionPreview == null
          ? 'Keep the leader landing separate from its anchor; release here to cancel.'
          : 'Move label landing · leader anchor stays fixed · Esc cancels';
    }
    if (d['annotation']['kind'] == 'angle') {
      v.hint = offset == null
          ? 'Keep the arc radius greater than zero; release here to cancel.'
          : 'Arc radius ${offset.abs().toStringAsPrecision(6)} ${m.units} · measured angle unchanged · Esc cancels';
    } else if (d['annotation']['kind'] == 'radius') {
      v.hint = offset == null
          ? 'Enter a finite label offset.'
          : 'Label offset ${offset.toStringAsPrecision(6)} ${m.units} · measured radius unchanged · Esc cancels';
    }
    v.changed();
  }

  Future<void> dimensionMenu(Json d, Offset position) async {
    await showDimensionMenu(context, m, d, position);
    if (!mounted) return;
    v.hint = m.message;
    v.changed();
    widget.onFocus?.call();
  }

  Future<void> commitDimension(Json annotation, int revision) async {
    await m.editDimension(revision, annotation);
    if (!mounted) return;
    v.hint = m.message;
    v.changed();
  }

  Json? hitGuide(Offset p) {
    if (!v.guidesShown ||
        p.dx < 22 ||
        p.dy < 22 ||
        !(Offset.zero & v.size).contains(p)) {
      return null;
    }
    Json? closest;
    double distance = 5.001;
    for (final guide in v.guides.reversed.where((g) => g['visible'] == true)) {
      final horizontal = guide['orientation'] == 'horizontal';
      final at = v.screen(
        horizontal
            ? Offset(0, number(guide['position']))
            : Offset(number(guide['position']), 0),
      );
      final d = horizontal ? (p.dy - at.dy).abs() : (p.dx - at.dx).abs();
      if (d < distance) {
        closest = guide;
        distance = d;
      }
    }
    return closest;
  }

  Offset guidePoint(Offset location) {
    var q = v.world(location);
    if (v.draggedGuideId != null &&
        guideStartPosition != null &&
        down != null) {
      final start = v.world(down!);
      q = v.guideOrientation == 'horizontal'
          ? Offset(q.dx, guideStartPosition! + q.dy - start.dy)
          : Offset(guideStartPosition! + q.dx - start.dx, q.dy);
    }
    return v.snapWorld(q, excludeGuideId: v.draggedGuideId);
  }

  Future<void> editGuide(Json guide, Offset position) async {
    await showGuideMenu(context, m, Map<String, dynamic>.from(guide), position);
    if (!mounted) return;
    v.hint = m.message;
    v.changed();
    widget.onFocus?.call();
  }

  Future<void> commitGuide(Json action, int revision) async {
    await applyGuideAction(m, revision, action);
    if (!mounted) return;
    v.hint = m.message;
    v.changed();
  }

  void movePreview(Offset location) {
    if (down == null || moveAnchor == null) return;
    final target = moveAnchor! + v.world(location) - v.world(down!);
    v.dragDelta = v.snapWorld(target, exclude: m.selection) - moveAnchor!;
    v.changed();
  }

  bool get staleGesture =>
      downEpoch != v.gestureEpoch ||
      downRevision != m.state['revision'] ||
      (draggingDimension != null &&
          (downScale != v.scale || downTranslation != v.translation));

  void cancelGesture() {
    draggingDimension = null;
    v.dimensionPreview = null;
    guideGesture = false;
    guideStartPosition = null;
    v.draggedGuideId = v.hoveredGuideId = null;
    v.guideOrientation = null;
    v.guidePosition = null;
    widget.pointEditor?.cancelDrag();
    pointGesture = false;
    down = moveAnchor = null;
    panning = box = moving = dragging = false;
    v.start = v.current = null;
    v.dragDelta = Offset.zero;
    v.snapTarget = null;
    v.changed();
  }

  Future<void> click(Offset location) async {
    if (m.busy || !m.ready) return;
    final p = v.snapped(location);
    if (v.tool == 'dimension') {
      if (m.readOnly || widget.onDimension == null) return;
      final result = await m.request('design.dimension_pick', {
        'x': p.dx,
        'y': p.dy,
        'tolerance': 10 / v.scale,
      });
      if (!mounted || v.tool != 'dimension') return;
      if (result['ok'] != true) {
        v.hint = result['error'].toString();
        v.changed();
        return;
      }
      final ref = result['reference'] as Json;
      if (v.dimensionStart == null) {
        v.dimensionStart = ref;
        v.start = point(ref['fallback']);
        v.current = v.start;
        v.hint =
            'Click the second dimension point. Nearby geometry features stay linked.';
        v.changed();
      } else {
        final first = v.dimensionStart!;
        v.setTool('select');
        await widget.onDimension!([first, ref]);
      }
      return;
    }
    if (v.tool == 'select') {
      final ids = hit(location);
      final next = shift ? {...m.selection} : <String>{};
      for (final id in ids) {
        if (shift && next.contains(id)) {
          next.remove(id);
        } else {
          next.add(id);
        }
      }
      await m.select(next);
      return;
    }
    if (v.tool == 'pan') return;
    if (v.start == null) {
      v.start = p;
      v.current = p;
      v.changed();
      return;
    }
    final a = v.start!;
    v.start = null;
    v.current = null;
    if (v.tool == 'measure') {
      v.hint =
          'Distance ${(p - a).distance.toStringAsFixed(4)} ${m.units} · ΔX ${(p.dx - a.dx).toStringAsFixed(4)} · ΔY ${(p.dy - a.dy).toStringAsFixed(4)}';
      v.changed();
      return;
    }
    if (v.tool == 'cleaveLine') {
      if ((p - a).distance < 1e-10) {
        v.hint = 'Pick two different points across the closed outline.';
        v.changed();
        return;
      }
      v.setTool('select');
      await widget.onCleave?.call([
        {'x': a.dx, 'y': a.dy},
        {'x': p.dx, 'y': p.dy},
      ]);
      return;
    }
    final rect = Rect.fromPoints(a, p);
    final params = <String, dynamic>{'layer': widget.layer};
    if (v.tool == 'line') {
      await m.run('geometry.add_line', {
        ...params,
        'x1': a.dx,
        'y1': a.dy,
        'x2': p.dx,
        'y2': p.dy,
      });
    } else if (v.tool == 'circle') {
      if ((p - a).distance > 0) {
        await m.run('geometry.add_circle', {
          ...params,
          'x': a.dx,
          'y': a.dy,
          'diameter': 2 * (p - a).distance,
        });
      }
    } else if (rect.width > 0 && rect.height > 0) {
      final data = {
        ...params,
        'x': rect.left,
        'y': rect.top,
        'width': rect.width,
        'height': rect.height,
      };
      await m.run(
        v.tool == 'rectangle' ? 'geometry.add_rectangle' : 'design.shape',
        {...data, if (v.tool != 'rectangle') 'kind': v.tool},
      );
    }
    v.changed();
  }

  @override
  Widget build(BuildContext context) {
    v.snapGridStep = m.units == 'mm' ? .125 * 25.4 : .125;
    v.guides = (m.state['guides'] as List? ?? []).cast<Json>();
    if (!(m.state['dimensions'] as List? ?? []).any(
      (d) =>
          d['id'] == v.selectedDimensionId &&
          m.visible(d['annotation'] as Json),
    )) {
      v.selectedDimensionId = null;
    }
    cache();
    return LayoutBuilder(
      builder: (context, c) {
        v.size = Size(c.maxWidth, c.maxHeight);
        final hoveredDimension = (m.state['dimensions'] as List? ?? [])
            .cast<Json>()
            .where(
              (d) =>
                  d['id'] == v.hoveredDimensionId &&
                  m.visible(d['annotation'] as Json),
            )
            .firstOrNull;
        final hoveredGuide = v.guides
            .where(
              (g) =>
                  v.guidesShown &&
                  g['visible'] == true &&
                  g['id'] == v.hoveredGuideId,
            )
            .firstOrNull;
        return MouseRegion(
          cursor: hoveredDimension != null && v.tool == 'select'
              ? (m.readOnly || m.locked(hoveredDimension['annotation'] as Json)
                    ? SystemMouseCursors.forbidden
                    : !canDragDimension(hoveredDimension)
                    ? SystemMouseCursors.click
                    : hoveredDimension['annotation']['kind'] == 'horizontal'
                    ? SystemMouseCursors.resizeUpDown
                    : hoveredDimension['annotation']['kind'] == 'vertical'
                    ? SystemMouseCursors.resizeLeftRight
                    : SystemMouseCursors.move)
              : hoveredGuide != null &&
                    v.tool == 'select' &&
                    widget.pointEditor?.active != true
              ? (hoveredGuide['locked'] == true || m.readOnly
                    ? SystemMouseCursors.forbidden
                    : hoveredGuide['orientation'] == 'horizontal'
                    ? SystemMouseCursors.resizeUpDown
                    : SystemMouseCursors.resizeLeftRight)
              : v.tool == 'pan'
              ? SystemMouseCursors.grab
              : v.tool == 'select'
              ? SystemMouseCursors.basic
              : SystemMouseCursors.precise,
          onHover: (e) {
            updatePointer(e.localPosition);
          },
          onExit: (_) {
            if (v.hoveredDimensionId != null) {
              setState(() => v.hoveredDimensionId = null);
            }
            if (v.hoveredGuideId != null) {
              setState(() => v.hoveredGuideId = null);
            }
            v.snapTarget = null;
            v.cursorInside = false;
            v.hoveredIds = [];
            v.changed();
          },
          child: Listener(
            onPointerSignal: (e) {
              if (e is PointerScrollEvent) {
                if (guideGesture || draggingDimension != null) cancelGesture();
                if (widget.pointEditor?.dragging == true) {
                  widget.pointEditor!.cancelDrag();
                }
                GestureBinding.instance.pointerSignalResolver.register(e, (
                  event,
                ) {
                  v.zoom(math.exp(-e.scrollDelta.dy * .0015), e.localPosition);
                  updatePointer(e.localPosition);
                });
              }
            },
            onPointerDown: (e) {
              widget.onFocus?.call();
              updatePointer(e.localPosition);
              if (e.buttons == kSecondaryMouseButton &&
                  !m.busy &&
                  m.ready &&
                  widget.pointEditor?.active != true) {
                final dimension = v.tool == 'select'
                    ? dimensionAt(e.localPosition)
                    : null;
                if (dimension != null) {
                  v.selectedDimensionId = dimension['id'] as String;
                  v.changed();
                  dimensionMenu(dimension, e.position);
                  return;
                }
                final guide = v.tool == 'select'
                    ? hitGuide(e.localPosition)
                    : null;
                if (guide != null) {
                  editGuide(guide, e.position);
                } else {
                  widget.onContextMenu?.call(e.position);
                }
                return;
              }
              if (m.busy ||
                  !m.ready ||
                  (e.buttons != kPrimaryMouseButton &&
                      e.buttons != kMiddleMouseButton)) {
                return;
              }
              down = e.localPosition;
              downEpoch = v.gestureEpoch;
              downRevision = m.state['revision'] ?? -1;
              downScale = v.scale;
              downTranslation = v.translation;
              if (e.buttons == kPrimaryMouseButton &&
                  !shift &&
                  v.tool == 'select' &&
                  widget.pointEditor?.active != true) {
                final d = dimensionAt(e.localPosition);
                v.selectedDimensionId = d?['id'] as String?;
                if (d != null) {
                  final annotation = d['annotation'] as Json;
                  v.hint = m.readOnly
                      ? 'This drawing is view-only.'
                      : m.locked(annotation)
                      ? 'Dimension layer locked. Use Layers to unlock it.'
                      : !canDragDimension(d)
                      ? (annotation['kind'] == 'leader' ||
                                annotation['kind'] == 'note'
                            ? 'Landing linked to geometry. Right-click to edit its reference; choose Free coordinates to move it freely.'
                            : 'Right-click to edit this dimension.')
                      : hasFreeDimensionLanding(d)
                      ? 'Drag label or square handle to move its landing · leader anchor stays fixed · Esc cancels'
                      : annotation['kind'] == 'radius'
                      ? 'Drag the label or diamond along the radius line · measured radius stays fixed · Esc cancels'
                      : annotation['kind'] == 'angle'
                      ? 'Drag the arc, label or round handle to change arc radius · measured angle stays fixed · Esc cancels'
                      : 'Drag to change dimension offset · right-click to edit/delete · Esc cancels';
                  if (m.readOnly ||
                      m.locked(annotation) ||
                      m.state['dimensionAuthoring'] != true ||
                      !canDragDimension(d)) {
                    down = null;
                  } else {
                    draggingDimension = d;
                    v.dimensionPreview = d;
                  }
                  v.snapTarget = null;
                  v.changed();
                  return;
                }
              } else if (e.buttons == kPrimaryMouseButton) {
                v.selectedDimensionId = null;
              }
              final guide =
                  e.buttons == kPrimaryMouseButton &&
                      !shift &&
                      v.tool == 'select' &&
                      widget.pointEditor?.active != true
                  ? hitGuide(e.localPosition)
                  : null;
              if (guide != null) {
                if (guide['locked'] == true ||
                    m.readOnly ||
                    m.state['guideAuthoring'] != true) {
                  v.hint = m.readOnly
                      ? 'This drawing is view-only.'
                      : 'Guide locked. Right-click to unlock it.';
                  down = null;
                  v.changed();
                  return;
                }
                guideGesture = true;
                guideStartPosition = number(guide['position']);
                v.draggedGuideId = guide['id'] as String;
                v.guideOrientation = guide['orientation'] as String;
                v.guidePosition = guideStartPosition;
                v.start = v.current = null;
                v.snapTarget = null;
                v.changed();
                return;
              }
              guideGesture =
                  e.buttons == kPrimaryMouseButton &&
                  !m.readOnly &&
                  m.state['guideAuthoring'] == true &&
                  (e.localPosition.dy < 22 || e.localPosition.dx < 22);
              if (guideGesture) {
                v.draggedGuideId = null;
                widget.pointEditor?.cancelDrag();
                v.start = null;
                v.current = null;
                v.dimensionStart = null;
                v.guideOrientation = e.localPosition.dy < 22
                    ? 'horizontal'
                    : 'vertical';
                final p = v.snapped(e.localPosition);
                v.guidePosition = v.guideOrientation == 'horizontal'
                    ? p.dy
                    : p.dx;
                v.guidesShown = true;
                v.changed();
                return;
              }
              panning = v.tool == 'pan' || e.buttons == kMiddleMouseButton;
              box = false;
              moving =
                  !panning &&
                  !m.readOnly &&
                  !shift &&
                  v.tool == 'select' &&
                  hit(e.localPosition).intersection(m.selection).isNotEmpty;
              dragging = false;
              moveAnchor = moving && v.snap
                  ? v.snapIndex
                        .nearest(
                          v.world(e.localPosition),
                          10 / v.scale,
                          only: m.selection,
                        )
                        ?.position
                  : null;
              moveAnchor ??= v.world(e.localPosition);
              final editor = widget.pointEditor;
              pointGesture = !panning && editor?.active == true;
              if (pointGesture) {
                final index = editor!.hit(e.localPosition, v.screen);
                if (editor.inserting) {
                  final p = snapEditedPoint(v.world(e.localPosition));
                  editor.edit('insert', {'x': p.dx, 'y': p.dy});
                } else if (index != null) {
                  editor.press(
                    index,
                    e.localPosition,
                    v.world(e.localPosition),
                    additive:
                        shift || HardwareKeyboard.instance.isControlPressed,
                  );
                } else {
                  final ids = hitObjects(e.localPosition);
                  if (ids.isNotEmpty && ids.first != editor.entityId) {
                    editor.activate(ids.first);
                  } else {
                    editor.press(
                      null,
                      e.localPosition,
                      v.world(e.localPosition),
                      additive:
                          shift || HardwareKeyboard.instance.isControlPressed,
                    );
                  }
                }
              }
            },
            onPointerMove: (e) {
              if (down != null && staleGesture) {
                cancelGesture();
                return;
              }
              if (draggingDimension != null) {
                dimensionMotion(e.localPosition);
                return;
              }
              if (guideGesture) {
                if (v.guideOrientation != null) {
                  v.cursor = v.world(e.localPosition);
                  v.cursorInside = (Offset.zero & v.size).contains(
                    e.localPosition,
                  );
                  final p = guidePoint(e.localPosition);
                  v.guidePosition = v.guideOrientation == 'horizontal'
                      ? p.dy
                      : p.dx;
                  v.changed();
                }
                return;
              } else if (pointGesture) {
                widget.pointEditor?.motion(
                  e.localPosition,
                  v.world(e.localPosition),
                  snap: v.snap,
                  snapper: snapEditedPoint,
                );
              } else if (panning) {
                v.translation += e.delta;
                v.changed();
              } else if (moving &&
                  down != null &&
                  (e.localPosition - down!).distance > 5) {
                dragging = true;
                movePreview(e.localPosition);
              } else if (!moving &&
                  v.tool == 'select' &&
                  down != null &&
                  (e.localPosition - down!).distance > 5) {
                box = true;
                v.start = v.world(down!);
                v.current = v.world(e.localPosition);
                v.changed();
              }
              updatePointer(e.localPosition);
            },
            onPointerUp: (e) {
              if (down == null) return;
              if (staleGesture) {
                cancelGesture();
                return;
              }
              if (draggingDimension != null) {
                final d = draggingDimension!, a = d['annotation'] as Json;
                final inside =
                    e.localPosition.dx >= 22 &&
                    e.localPosition.dy >= 22 &&
                    (Offset.zero & v.size).contains(e.localPosition);
                if (inside &&
                    (e.localPosition - down!).distance > 4 &&
                    !m.readOnly &&
                    !m.locked(a) &&
                    m.visible(a)) {
                  dimensionMotion(e.localPosition);
                  final preview = v.dimensionPreview?['annotation'] as Json?;
                  if (preview != null &&
                      dimensionPlacementChanged(a, preview)) {
                    commitDimension(preview, downRevision);
                  }
                }
                draggingDimension = null;
                v.dimensionPreview = null;
                down = null;
                v.changed();
                return;
              }
              if (guideGesture) {
                final inside =
                    e.localPosition.dx >= 22 &&
                    e.localPosition.dy >= 22 &&
                    (Offset.zero & v.size).contains(e.localPosition);
                if (inside &&
                    v.guideOrientation != null &&
                    (e.localPosition - down!).distance > 4 &&
                    !m.readOnly) {
                  final p = guidePoint(e.localPosition);
                  final position = v.guideOrientation == 'horizontal'
                      ? p.dy
                      : p.dx;
                  if (v.draggedGuideId == null) {
                    m.run('design.guide_add', {
                      'orientation': v.guideOrientation,
                      'position': position,
                    });
                  } else if (position != guideStartPosition && v.guidesShown) {
                    final guide = v.guides
                        .where((g) => g['id'] == v.draggedGuideId)
                        .firstOrNull;
                    if (guide != null &&
                        guide['visible'] == true &&
                        guide['locked'] != true) {
                      commitGuide({
                        'operation': 'edit',
                        'id': v.draggedGuideId,
                        'position': position,
                      }, downRevision);
                    }
                  }
                }
                v.guideOrientation = null;
                v.guidePosition = null;
                v.snapTarget = null;
                guideGesture = false;
                guideStartPosition = null;
                v.draggedGuideId = null;
                down = null;
                v.changed();
                return;
              } else if (pointGesture) {
                final inside = (Offset.zero & v.size).contains(e.localPosition);
                if (inside) {
                  widget.pointEditor?.motion(
                    e.localPosition,
                    v.world(e.localPosition),
                    snap: v.snap,
                    snapper: snapEditedPoint,
                  );
                }
                widget.pointEditor?.release(inside: inside);
              } else if (dragging) {
                if ((Offset.zero & v.size).contains(e.localPosition)) {
                  movePreview(e.localPosition);
                  if (v.dragDelta != Offset.zero) {
                    m.run('geometry.transform', {
                      'dx': v.dragDelta.dx,
                      'dy': v.dragDelta.dy,
                    });
                  }
                }
                v.dragDelta = Offset.zero;
                v.changed();
              } else if (box) {
                final rect = Rect.fromPoints(v.start!, v.current!);
                final ids = m.selectable
                    .where((o) {
                      final b = bounds(o['bounds']);
                      return b != null && rect.overlaps(b.inflate(.0001));
                    })
                    .map((e) => e['id'] as String)
                    .toSet();
                m.select(shift ? {...m.selection, ...ids} : ids);
                v.start = null;
                v.current = null;
                v.changed();
              } else if (!panning) {
                click(e.localPosition);
              }
              panning = false;
              pointGesture = false;
              down = null;
              box = false;
              moving = dragging = false;
              v.snapTarget = null;
              v.changed();
            },
            onPointerCancel: (_) {
              cancelGesture();
            },
            child: RepaintBoundary(
              child: AnimatedBuilder(
                animation: Listenable.merge([v, widget.pointEditor]),
                builder: (context, _) => CustomPaint(
                  size: Size.infinite,
                  painter: DesignPainter(
                    m,
                    v,
                    paths,
                    widget.dark,
                    widget.pointEditor,
                  ),
                ),
              ),
            ),
          ),
        );
      },
    );
  }
}

class DesignPainter extends CustomPainter {
  DesignPainter(this.m, this.v, this.paths, this.dark, [this.pointEditor]);
  final PointEditor? pointEditor;
  final DesignController m;
  final DesignViewport v;
  final Map<String, Path> paths;
  final bool dark;
  final teal = const Color(0xff0b8d84);
  void text(
    Canvas c,
    String value,
    Offset at, {
    double size = 10,
    Color color = const Color(0xff728184),
  }) {
    final tp = TextPainter(
      text: TextSpan(
        text: value,
        style: TextStyle(fontSize: size, color: color, fontFamily: 'Segoe UI'),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    tp.paint(c, at);
  }

  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawRect(
      Offset.zero & size,
      Paint()..color = dark ? const Color(0xff18272b) : const Color(0xffe8eeeb),
    );
    canvas.save();
    canvas.clipRect(Offset.zero & size);
    canvas.translate(v.translation.dx, v.translation.dy);
    canvas.scale(v.scale, -v.scale);
    final stroke = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1 / v.scale;
    for (final p in m.plates) {
      final r = Rect.fromLTWH(
        number(p['x']),
        number(p['y']),
        number(p['width']),
        number(p['height']),
      );
      canvas.drawRect(
        r,
        Paint()
          ..color = dark ? const Color(0xff26383c) : const Color(0xfffefefd),
      );
      canvas.drawRect(r, stroke..color = const Color(0xffd27a71));
    }
    final topLeft = v.world(Offset.zero),
        bottomRight = v.world(Offset(size.width, size.height));
    final step = math
        .pow(10, (math.log(65 / v.scale) / math.ln10).floor())
        .toDouble();
    if (v.grid) {
      stroke.color = dark ? const Color(0xff35464a) : const Color(0xffe2e9e5);
      for (
        double x = (topLeft.dx / step).floor() * step;
        x < bottomRight.dx;
        x += step
      ) {
        canvas.drawLine(
          Offset(x, bottomRight.dy),
          Offset(x, topLeft.dy),
          stroke,
        );
      }
      for (
        double y = (bottomRight.dy / step).floor() * step;
        y < topLeft.dy;
        y += step
      ) {
        canvas.drawLine(
          Offset(topLeft.dx, y),
          Offset(bottomRight.dx, y),
          stroke,
        );
      }
    }
    for (final area
        in (m.state['patchworkAreas'] as List? ?? []).cast<Json>()) {
      final r = bounds(area['bounds']);
      if (r != null) {
        canvas.drawRect(
          r,
          Paint()..color = const Color(0xff008577).withValues(alpha: .07),
        );
        canvas.drawRect(r, stroke..color = const Color(0xff008577));
      }
    }
    for (final e in m.entities) {
      if (!m.visible(e)) continue;
      final preview = e['id'] == pointEditor?.entityId
          ? pointEditor?.previewEntity
          : null;
      final path = preview == null ? paths[e['id']] : entityPath(preview);
      if (path == null) continue;
      final selected = m.selection.contains(e['id']);
      final appearance = e['appearance'] as Json?;
      final hasFill = appearance?['fill_enabled'] == true;
      final hasStroke =
          appearance == null || appearance['stroke_enabled'] == true;
      if (!selected && !hasFill && !hasStroke) continue;
      canvas.save();
      if (selected) canvas.translate(v.dragDelta.dx, v.dragDelta.dy);
      final layer = m.layers.where((l) => l['name'] == e['layer']).firstOrNull;
      var color = teal;
      try {
        color = Color(
          int.parse(
            (layer?['color'] ?? '#087f75').toString().replaceFirst('#', 'ff'),
            radix: 16,
          ),
        );
      } catch (_) {}
      if (dark && color.computeLuminance() < .2) {
        color = const Color(0xff8dd5ca);
      }
      if (appearance != null && hasStroke) {
        color = previewColor(appearance['stroke'], color);
      }
      if (e['kind'] == 'text') {
        final p = point(e['insertion']);
        canvas.save();
        canvas.translate(p.dx, p.dy);
        canvas.rotate(number(e['rotation']) * math.pi / 180);
        canvas.scale(1, -1);
        final h = number(e['height']);
        final tp = TextPainter(
          text: TextSpan(
            text: e['text'],
            style: TextStyle(
              fontSize: h,
              color: selected
                  ? teal
                  : hasFill
                  ? previewColor(appearance!['fill'])
                  : color,
              fontFamily: e['font_family'],
            ),
          ),
          textDirection: TextDirection.ltr,
        )..layout();
        canvas.scale(
          number(e['width_factor']) == 0 ? 1 : number(e['width_factor']),
          1,
        );
        final align = e['alignment'];
        tp.paint(
          canvas,
          Offset(
            align == 'center'
                ? -tp.width / 2
                : align == 'right'
                ? -tp.width
                : 0,
            -h,
          ),
        );
        canvas.restore();
      } else {
        final closed =
            ['rectangle', 'circle'].contains(e['kind']) ||
            e['kind'] == 'polyline' && e['closed'] == true;
        if (hasFill && closed) {
          canvas.drawPath(
            path,
            Paint()..color = previewColor(appearance!['fill']),
          );
        } else if (selected) {
          canvas.drawPath(path, Paint()..color = teal.withValues(alpha: .10));
        }
        if (hasStroke || selected) {
          canvas.drawPath(
            path,
            stroke
              ..color = selected ? teal : color
              ..strokeWidth = selected
                  ? 2 / v.scale
                  : number(appearance?['stroke_width']) > 0
                  ? math.max(1 / v.scale, number(appearance?['stroke_width']))
                  : 1.1 / v.scale,
          );
        }
      }
      if (selected && e['id'] != pointEditor?.entityId) {
        final r = bounds(e['bounds']);
        if (r != null) {
          canvas.drawRect(
            r.inflate(3 / v.scale),
            stroke
              ..color = teal.withValues(alpha: .6)
              ..strokeWidth = .8 / v.scale,
          );
        }
      }
      canvas.restore();
    }
    for (final d in (m.state['dimensions'] as List? ?? []).cast<Json>()) {
      if (!m.visible(d['annotation'] as Json)) {
        continue;
      }
      final display = v.dimensionPreview?['id'] == d['id']
          ? v.dimensionPreview!
          : d;
      paintDimension(
        canvas,
        display,
        v.scale,
        dark: dark,
        selected: v.selectedDimensionId == d['id'],
        hovered: v.hoveredDimensionId == d['id'],
      );
    }
    if (v.start != null && v.current != null) {
      stroke
        ..color = teal
        ..strokeWidth = 1.3 / v.scale;
      if (v.tool == 'line' ||
          v.tool == 'measure' ||
          v.tool == 'dimension' ||
          v.tool == 'cleaveLine') {
        canvas.drawLine(v.start!, v.current!, stroke);
      } else if (v.tool == 'circle') {
        canvas.drawCircle(v.start!, (v.current! - v.start!).distance, stroke);
      } else {
        canvas.drawRect(Rect.fromPoints(v.start!, v.current!), stroke);
      }
    }
    canvas.restore();
    final editor = pointEditor;
    void guideLine(
      String orientation,
      double position, {
      bool locked = false,
      bool preview = false,
      bool highlighted = false,
    }) {
      final horizontal = orientation == 'horizontal';
      final location = v.screen(
        horizontal ? Offset(0, position) : Offset(position, 0),
      );
      final axis = horizontal ? location.dy : location.dx;
      if (axis < 22 || axis > (horizontal ? size.height : size.width)) return;
      final paint = Paint()
        ..color = preview || highlighted
            ? Colors.orange
            : (dark ? const Color(0xffb4c7f5) : const Color(0xff536d9e))
        ..strokeWidth = preview || highlighted ? 2 : 1;
      final end = horizontal ? size.width : size.height;
      final dash = locked ? 2.0 : 6.0;
      for (double p = 22; p < end; p += dash + 3) {
        canvas.drawLine(
          horizontal ? Offset(p, axis) : Offset(axis, p),
          horizontal
              ? Offset(math.min(p + dash, end), axis)
              : Offset(axis, math.min(p + dash, end)),
          paint,
        );
      }
    }

    if (v.guidesShown) {
      for (final guide in (m.state['guides'] as List? ?? []).cast<Json>().where(
        (g) => g['visible'] == true,
      )) {
        if (guide['id'] == v.draggedGuideId) continue;
        guideLine(
          guide['orientation'] as String,
          number(guide['position']),
          locked: guide['locked'] == true,
          highlighted: guide['id'] == v.hoveredGuideId,
        );
      }
    }
    if (v.guideOrientation != null && v.guidePosition != null) {
      guideLine(v.guideOrientation!, v.guidePosition!, preview: true);
    }
    final hoveredGuide = v.guides
        .where(
          (g) =>
              v.guidesShown &&
              g['visible'] == true &&
              g['id'] == v.hoveredGuideId,
        )
        .firstOrNull;
    if (v.cursorInside &&
        (v.guideOrientation != null || hoveredGuide != null)) {
      final orientation = v.guideOrientation ?? hoveredGuide!['orientation'];
      final coordinate = v.guidePosition ?? number(hoveredGuide!['position']);
      final label =
          '${orientation == 'horizontal' ? 'Y' : 'X'} ${coordinate.toStringAsFixed(4)} ${m.units}'
          '${hoveredGuide?['locked'] == true ? ' · locked' : ' · guide'}';
      final at = v.screen(v.cursor);
      final labelAt = Offset(
        (at.dx + 12).clamp(24.0, math.max(24.0, size.width - 195)),
        (at.dy + 24).clamp(24.0, math.max(24.0, size.height - 24)),
      );
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          labelAt & const Size(190, 20),
          const Radius.circular(3),
        ),
        Paint()
          ..color = dark ? const Color(0xff203337) : const Color(0xfffff5e5),
      );
      text(
        canvas,
        label,
        labelAt + const Offset(5, 2),
        size: 11,
        color: dark ? Colors.orange.shade200 : Colors.brown,
      );
    }
    if (editor != null && editor.active) {
      canvas.save();
      canvas.clipRect(Offset.zero & size);
      final vertices = editor.displayed;
      for (var i = 0; i < vertices.length; i++) {
        final rect = Rect.fromCenter(
          center: v.screen(vertices[i]),
          width: 10,
          height: 10,
        );
        canvas.drawRect(
          rect,
          Paint()
            ..color = editor.selected.contains(i)
                ? Colors.orange
                : Colors.white,
        );
        canvas.drawRect(
          rect,
          Paint()
            ..color = teal
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1.5,
        );
      }
      canvas.restore();
    }
    canvas.drawRect(
      Rect.fromLTWH(0, 0, size.width, 22),
      Paint()..color = dark ? const Color(0xff203337) : const Color(0xfff4f7f5),
    );
    canvas.drawRect(
      Rect.fromLTWH(0, 22, 22, math.max(0, size.height - 22)),
      Paint()..color = dark ? const Color(0xff203337) : const Color(0xfff4f7f5),
    );
    for (
      double y = (bottomRight.dy / (step * 5)).ceil() * step * 5;
      y < topLeft.dy;
      y += step * 5
    ) {
      final py = v.screen(Offset(0, y)).dy;
      if (py > 30) {
        canvas.save();
        canvas.translate(4, py + 10);
        canvas.rotate(-math.pi / 2);
        text(canvas, y.toStringAsFixed(step < 1 ? 2 : 0), Offset.zero);
        canvas.restore();
      }
    }
    for (
      double x = (topLeft.dx / (step * 5)).ceil() * step * 5;
      x < bottomRight.dx;
      x += step * 5
    ) {
      final px = v.screen(Offset(x, 0)).dx;
      text(canvas, x.toStringAsFixed(step < 1 ? 2 : 0), Offset(px + 2, 4));
    }
    final target = v.snapTarget;
    if (v.snap && v.cursorInside && target != null) {
      final at = v.screen(target.position);
      if (at.dx >= 22 && at.dy >= 22 && (Offset.zero & size).contains(at)) {
        final color = dark ? const Color(0xffffcd74) : const Color(0xff9c5000);
        canvas.drawRect(
          Rect.fromCenter(center: at, width: 10, height: 10),
          Paint()
            ..color = color
            ..style = PaintingStyle.stroke
            ..strokeWidth = 2,
        );
        final labelAt = Offset(
          (at.dx + 12).clamp(24.0, math.max(24.0, size.width - 90)),
          (at.dy + 12).clamp(24.0, math.max(24.0, size.height - 24)),
        );
        canvas.drawRRect(
          RRect.fromRectAndRadius(
            labelAt & const Size(78, 20),
            const Radius.circular(3),
          ),
          Paint()
            ..color = dark ? const Color(0xff203337) : const Color(0xfffff5e5),
        );
        text(
          canvas,
          target.label,
          labelAt + const Offset(5, 2),
          size: 11,
          color: color,
        );
      }
    }
    final origin = v.screen(Offset.zero);
    canvas.drawCircle(origin, 3, Paint()..color = teal);
    text(canvas, 'X', origin + const Offset(19, -14), color: teal);
    text(canvas, 'Y', origin + const Offset(4, -33), color: teal);
    canvas.drawLine(
      origin,
      origin + const Offset(20, 0),
      Paint()..color = teal,
    );
    canvas.drawLine(
      origin,
      origin + const Offset(0, -20),
      Paint()..color = teal,
    );
  }

  @override
  bool shouldRepaint(covariant DesignPainter oldDelegate) => true;
}
