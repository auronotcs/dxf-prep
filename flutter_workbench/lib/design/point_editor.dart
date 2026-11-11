import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'controller.dart';

/// Owns a revision-bound preview; pointer motion never changes the document.
class PointEditor extends ChangeNotifier {
  PointEditor(this.model) {
    model.addListener(_documentChanged);
  }
  final DesignController model;
  bool active = false, inserting = false, committing = false;
  bool _disposed = false;
  int _generation = 0;
  String? entityId;
  int? revision;
  Json? source;
  Set<int> selected = {};
  List<Offset>? preview;
  Offset? pressWorld, pressScreen;
  int? anchor;
  Offset delta = Offset.zero;
  String hint = 'Click a line, rectangle or polyline to edit its points.';
  List<Offset> get points => source == null
      ? []
      : source!['kind'] == 'line'
      ? [point(source!['start']), point(source!['end'])]
      : (source!['points'] as List).map(point).toList();
  List<Offset> get displayed => preview ?? points;
  bool get dragging => pressWorld != null;
  bool get canEdit =>
      active && source != null && !model.busy && model.ready && !model.readOnly;
  void changed() {
    if (!_disposed) notifyListeners();
  }

  void _clearOwner() {
    _generation++;
    entityId = null;
    revision = null;
    source = null;
    selected = {};
    _clearDrag();
    inserting = false;
  }

  void _clearDrag() {
    preview = null;
    pressWorld = pressScreen = null;
    anchor = null;
    delta = Offset.zero;
  }

  void _documentChanged() {
    if (!active || committing) return;
    final e = model.entities.where((e) => e['id'] == entityId).firstOrNull;
    if (source != null &&
        (revision != model.state['revision'] ||
            model.readOnly ||
            !model.ready ||
            e == null ||
            !model.visible(e) ||
            model.locked(e) ||
            !setEquals(model.selection, {entityId!}))) {
      _clearOwner();
      hint = 'Drawing changed. Click a contour to continue point editing.';
      changed();
    }
  }

  void start() {
    _clearOwner();
    active = true;
    hint = 'Click a line, rectangle or polyline to edit its points.';
    changed();
  }

  void finish() {
    active = false;
    _clearOwner();
    changed();
  }

  void cancelDrag() {
    _clearDrag();
    hint = 'Drag cancelled. Select or drag a point; Done finishes.';
    changed();
  }

  void selectAll() {
    if (!canEdit) return;
    _clearDrag();
    selected = Set.of(List.generate(points.length, (i) => i));
    changed();
  }

  void toggleInsert() {
    if (!canEdit) return;
    _clearDrag();
    inserting = !inserting;
    hint = inserting
        ? 'Click near a contour segment to insert a point; Escape cancels.'
        : 'Select or drag a point.';
    changed();
  }

  Future<void> activate(String id) async {
    if (!active || model.busy || model.readOnly) return;
    final entity = model.selectable.where((e) => e['id'] == id).firstOrNull;
    if (entity == null ||
        !['line', 'rectangle', 'polyline'].contains(entity['kind'])) {
      hint =
          'Choose a line, rectangle or polyline. Convert curves to an editable outline first.';
      changed();
      return;
    }
    _clearOwner();
    final generation = _generation;
    committing = true;
    try {
      if (!await model.run('selection.set', {
        'entity_ids': [id],
      })) {
        return;
      }
      if (!active || generation != _generation || _disposed) return;
      final ok = await model.run('design.point_begin', {
        'entity_id': id,
        'expected_revision': model.state['revision'],
      });
      if (!active || generation != _generation || _disposed) return;
      if (ok) {
        entityId = id;
        _adopt();
        hint =
            'Drag points · Shift/Ctrl selects more · Delete removes points · Escape cancels';
      } else {
        hint = model.message;
      }
    } finally {
      committing = false;
      changed();
    }
  }

  void _adopt() {
    source = model.entities.where((e) => e['id'] == entityId).firstOrNull;
    revision = model.state['revision'] as int?;
  }

  int? hit(Offset location, Offset Function(Offset) screen) {
    int? result;
    double distance = 10;
    final vertices = displayed;
    for (var i = 0; i < vertices.length; i++) {
      final d = (screen(vertices[i]) - location).distance;
      if (d <= distance) {
        result = i;
        distance = d;
      }
    }
    return result;
  }

  void press(int? index, Offset screen, Offset world, {bool additive = false}) {
    if (!canEdit) return;
    _clearDrag();
    if (index == null) {
      if (!additive) selected.clear();
      changed();
      return;
    }
    if (additive) {
      if (!selected.remove(index)) selected.add(index);
    } else {
      if (!selected.contains(index)) selected = {index};
      anchor = index;
      pressWorld = world;
      pressScreen = screen;
    }
    hint =
        '${selected.length} point(s) selected · Drag to move · Delete points · Done finishes';
    changed();
  }

  void motion(
    Offset screen,
    Offset world, {
    required bool snap,
    Offset Function(Offset)? snapper,
  }) {
    if (!canEdit ||
        !dragging ||
        (screen - pressScreen!).distance < 3 && preview == null) {
      return;
    }
    final base = points;
    var target = base[anchor!] + world - pressWorld!;
    if (snap) {
      target =
          snapper?.call(target) ??
          Offset(target.dx.roundToDouble(), target.dy.roundToDouble());
    }
    delta = target - base[anchor!];
    preview = List.generate(
      base.length,
      (i) => selected.contains(i) ? base[i] + delta : base[i],
    );
    changed();
  }

  Future<void> release({required bool inside}) async {
    final move = delta;
    final apply = inside && preview != null && move != Offset.zero;
    _clearDrag();
    changed();
    if (apply) await edit('move', {'dx': move.dx, 'dy': move.dy});
  }

  Future<void> edit(String operation, [Json params = const {}]) async {
    if (!canEdit) return;
    _clearDrag();
    if (operation != 'insert' && selected.isEmpty) {
      hint = 'Select contour points first. No objects were deleted.';
      changed();
      return;
    }
    final generation = _generation;
    committing = true;
    try {
      final ok = await model.run('design.point_edit', {
        'entity_id': entityId,
        'expected_revision': revision,
        'operation': operation,
        'indices': selected.toList(),
        ...params,
      });
      if (!active || generation != _generation || _disposed) return;
      if (ok) {
        _adopt();
        if (operation != 'move') selected.clear();
        inserting = false;
        hint =
            '${operation == 'move'
                ? 'Moved'
                : operation == 'delete'
                ? 'Deleted'
                : 'Inserted'} points · Undo restores this edit';
      } else {
        hint = model.message;
      }
    } finally {
      committing = false;
      _documentChanged();
      changed();
    }
  }

  Json? get previewEntity => preview == null || source == null
      ? null
      : {
          ...source!,
          if (source!['kind'] == 'line') ...{
            'start': {'x': preview![0].dx, 'y': preview![0].dy},
            'end': {'x': preview![1].dx, 'y': preview![1].dy},
          } else
            'points': [
              for (final p in preview!) {'x': p.dx, 'y': p.dy},
            ],
        };
  @override
  void dispose() {
    _disposed = true;
    model.removeListener(_documentChanged);
    super.dispose();
  }
}
