import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'controller.dart';
import 'canvas.dart' show DesignViewport, entityPath;

class GuidesDialog extends StatefulWidget {
  const GuidesDialog({super.key, required this.model, required this.view});
  final DesignController model;
  final DesignViewport view;
  @override
  State<GuidesDialog> createState() => _GuidesDialogState();
}

class _GuidesDialogState extends State<GuidesDialog> {
  DesignController get m => widget.model;
  final coordinate = TextEditingController(text: '0');
  final name = TextEditingController();
  final actions = <Json>[];
  late List<Json> rows;
  late int revision;
  String orientation = 'horizontal',
      message = 'Changes stay in this draft until Apply.',
      selected = '';
  bool working = false, stale = false;
  String? token;
  Json? get current => rows.where((g) => g['id'] == selected).firstOrNull;
  bool get editable => !working && !stale && !m.readOnly;
  @override
  void initState() {
    super.initState();
    revision = m.state['revision'] as int;
    rows = (m.state['guides'] as List? ?? [])
        .map((g) => Map<String, dynamic>.from(g as Map))
        .toList();
    m.addListener(changed);
  }

  @override
  void dispose() {
    m.removeListener(changed);
    coordinate.dispose();
    name.dispose();
    super.dispose();
  }

  void changed() {
    if (mounted && m.state['revision'] != revision) {
      setState(() {
        stale = true;
        token = null;
        message =
            'Drawing changed. Close and reopen Guides before applying edits.';
      });
    }
  }

  void choose(Json row) {
    selected = row['id'] as String;
    coordinate.text = '${row['position']}';
    name.text = row['name'] as String;
    orientation = row['orientation'] as String;
  }

  Future<void> draft(Json action) async {
    if (!editable) return;
    setState(() {
      working = true;
      token = null;
    });
    try {
      final next = [...actions, action];
      final response = await m.request('design.guides_preview', {
        'actions': next,
      });
      if (!mounted) return;
      if (response['ok'] != true) {
        throw StateError(response['error'].toString());
      }
      if (m.state['revision'] != revision) {
        throw StateError('Drawing changed; reopen the manager.');
      }
      final preview = response['preview'] as Json;
      setState(() {
        actions.add(action);
        rows = (preview['guides'] as List).cast<Json>();
        token = preview['token'] as String;
        if (action['operation'] == 'add') selected = action['id'] as String;
        if (current != null) {
          choose(current!);
        } else {
          selected = '';
        }
        message =
            '${rows.length} guide(s) in draft. Apply records one undo step.';
      });
    } catch (e) {
      if (mounted) setState(() => message = e.toString());
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  Future<void> edit(bool add) async {
    final value = double.tryParse(coordinate.text);
    if (value == null || !value.isFinite) {
      setState(() => message = 'Enter a finite guide coordinate.');
      return;
    }
    await draft({
      'operation': add ? 'add' : 'edit',
      'id': add
          ? 'guide-${DateTime.now().microsecondsSinceEpoch}-${actions.length}'
          : selected,
      'orientation': orientation,
      'position': value,
      'name': name.text,
    });
  }

  Future<void> apply() async {
    if (!editable || token == null) return;
    setState(() => working = true);
    final ok = await m.run('design.guides_apply', {'token': token});
    if (!mounted) return;
    if (ok) {
      widget.view.guidesShown = true;
      widget.view.changed();
      Navigator.pop(context);
    } else {
      setState(() {
        working = false;
        token = null;
        message = m.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !working,
    child: AlertDialog(
      title: const Text('Construction guides'),
      content: SizedBox(
        width: 850,
        height: math.min(600, MediaQuery.sizeOf(context).height - 180),
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text(
                'Drag from the top ruler for a horizontal guide, or the left ruler for a vertical guide. Visible guides attract drawing tools when Snap is on. Locked guides still snap; they cannot move or be deleted.',
              ),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Show guides on canvas'),
                subtitle: const Text(
                  'View toggle only; does not change saved visibility.',
                ),
                value: widget.view.guidesShown,
                onChanged: (v) => setState(() {
                  widget.view.guidesShown = v!;
                  widget.view.changed();
                }),
              ),
              Wrap(
                spacing: 12,
                runSpacing: 8,
                children: [
                  SizedBox(
                    width: 170,
                    child: DropdownButtonFormField<String>(
                      isExpanded: true,
                      key: ValueKey('orientation-$orientation'),
                      initialValue: orientation,
                      decoration: const InputDecoration(
                        labelText: 'Orientation',
                      ),
                      items: ['horizontal', 'vertical']
                          .map(
                            (v) => DropdownMenuItem(value: v, child: Text(v)),
                          )
                          .toList(),
                      onChanged: editable
                          ? (v) => setState(() {
                              orientation = v!;
                              selected = '';
                            })
                          : null,
                    ),
                  ),
                  SizedBox(
                    width: 170,
                    child: TextField(
                      key: const ValueKey('guide-coordinate'),
                      controller: coordinate,
                      enabled: editable,
                      decoration: InputDecoration(
                        labelText:
                            '${orientation == 'horizontal' ? 'Y' : 'X'} coordinate (${m.units})',
                      ),
                    ),
                  ),
                  SizedBox(
                    width: 220,
                    child: TextField(
                      controller: name,
                      enabled: editable,
                      decoration: const InputDecoration(
                        labelText: 'Name (optional)',
                      ),
                    ),
                  ),
                  TextButton(
                    onPressed: editable ? () => edit(true) : null,
                    child: const Text('Add guide'),
                  ),
                  TextButton(
                    onPressed:
                        editable &&
                            current != null &&
                            current!['locked'] != true
                        ? () => edit(false)
                        : null,
                    child: const Text('Update coordinate / name'),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              SizedBox(
                height: 220,
                child: Row(
                  children: [
                    Expanded(
                      child: ListView(
                        children: [
                          for (final row in rows)
                            ListTile(
                              selected: row['id'] == selected,
                              onTap: working
                                  ? null
                                  : () => setState(() => choose(row)),
                              title: Text(
                                '${row['orientation'] == 'horizontal' ? 'Y' : 'X'} ${row['position']} ${m.units} · ${row['name']}',
                              ),
                              subtitle: Text(
                                '${row['visible'] == true ? 'Visible' : 'Hidden'} · ${row['locked'] == true ? 'Locked' : 'Unlocked'}',
                              ),
                            ),
                        ],
                      ),
                    ),
                    Expanded(
                      child: ColoredBox(
                        color: Colors.white,
                        child: CustomPaint(
                          size: Size.infinite,
                          painter: GuidePreview(m, rows, selected),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  TextButton(
                    onPressed: editable && current != null
                        ? () => draft({
                            'operation': 'visible',
                            'id': selected,
                            'value': current!['visible'] != true,
                          })
                        : null,
                    child: const Text('Show / Hide'),
                  ),
                  TextButton(
                    onPressed: editable && current != null
                        ? () => draft({
                            'operation': 'locked',
                            'id': selected,
                            'value': current!['locked'] != true,
                          })
                        : null,
                    child: const Text('Lock / Unlock'),
                  ),
                  TextButton(
                    onPressed:
                        editable &&
                            current != null &&
                            current!['locked'] != true
                        ? () => draft({'operation': 'delete', 'id': selected})
                        : null,
                    child: const Text('Delete from draft'),
                  ),
                  for (final entry in {
                    'Show all': ('show_all', true),
                    'Hide all': ('show_all', false),
                    'Lock all': ('lock_all', true),
                    'Unlock all': ('lock_all', false),
                  }.entries)
                    TextButton(
                      onPressed: editable
                          ? () => draft({
                              'operation': entry.value.$1,
                              'value': entry.value.$2,
                            })
                          : null,
                      child: Text(entry.key),
                    ),
                ],
              ),
              Text(message),
              if (m.readOnly)
                const Text(
                  'Machining jobs are view-only. Guide editing is disabled.',
                ),
              const Text(
                'Guides are drafting aids, separate from cutting geometry, nesting and output.',
              ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: working ? null : () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: editable && token != null ? apply : null,
          child: const Text('Apply'),
        ),
      ],
    ),
  );
}

class GuidePreview extends CustomPainter {
  GuidePreview(this.model, this.guides, this.selected);
  final DesignController model;
  final List<Json> guides;
  final String selected;
  @override
  void paint(Canvas canvas, Size size) {
    var box =
        bounds(model.state['bounds']) ??
        bounds(
          model.plates.firstOrNull == null
              ? null
              : [
                  model.plates.first['x'],
                  model.plates.first['y'],
                  number(model.plates.first['x']) +
                      number(model.plates.first['width']),
                  number(model.plates.first['y']) +
                      number(model.plates.first['height']),
                ],
        ) ??
        const Rect.fromLTWH(0, 0, 10, 10);
    for (final guide in guides.where((g) => g['visible'] == true)) {
      final position = number(guide['position']);
      box = box.expandToInclude(
        guide['orientation'] == 'horizontal'
            ? Rect.fromLTWH(box.left, position, box.width, 0)
            : Rect.fromLTWH(position, box.top, 0, box.height),
      );
    }
    final scale = math.min(
      (size.width - 24) / math.max(box.width, .001),
      (size.height - 24) / math.max(box.height, .001),
    );
    canvas.save();
    canvas.clipRect(Offset.zero & size);
    canvas.translate(size.width / 2, size.height / 2);
    canvas.scale(scale, -scale);
    canvas.translate(-box.center.dx, -box.center.dy);
    for (final e in model.entities.where(model.visible)) {
      canvas.drawPath(
        entityPath(e),
        Paint()
          ..color = Colors.grey
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1 / scale,
      );
    }
    for (final guide in guides.where((g) => g['visible'] == true)) {
      final horizontal = guide['orientation'] == 'horizontal',
          p = number(guide['position']);
      canvas.drawLine(
        horizontal
            ? Offset(box.left - 12 / scale, p)
            : Offset(p, box.top - 12 / scale),
        horizontal
            ? Offset(box.right + 12 / scale, p)
            : Offset(p, box.bottom + 12 / scale),
        Paint()
          ..color = guide['id'] == selected
              ? Colors.orange
              : const Color(0xff536d9e)
          ..strokeWidth = 1.5 / scale,
      );
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(covariant GuidePreview oldDelegate) => true;
}
