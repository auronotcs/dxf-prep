import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'controller.dart';
import 'shape_tools_dialog.dart';

class PatchworkDialog extends StatefulWidget {
  const PatchworkDialog({super.key, required this.model});
  final DesignController model;
  @override
  State<PatchworkDialog> createState() => _PatchworkDialogState();
}

class _PatchworkDialogState extends State<PatchworkDialog> {
  DesignController get m => widget.model;
  bool get packingAvailable => m.state['patchworkPacking'] == true;
  late Json plate;
  late final Json originalPlate;
  late List<Json> areas;
  final fields = <String, TextEditingController>{};
  String? selected, token;
  String
  direction = 'x',
  message =
      'Add pieces, review the layout, then Apply. Coordinates are local to the active plate.';
  bool working = false;
  @override
  void initState() {
    super.initState();
    plate = m.plates.firstWhere(
      (p) => p['number'] == m.state['activePlate'],
      orElse: () => m.plates.first,
    );
    originalPlate = Map<String, dynamic>.from(plate);
    areas = (m.state['patchworkAreas'] as List? ?? [])
        .cast<Json>()
        .where((a) => a['plate_number'] == plate['number'])
        .map((a) => Map<String, dynamic>.from(a))
        .toList();
    final factor = m.units == 'mm' ? 25.4 : 1;
    for (final e in {
      'name': 'Piece',
      'x': '0',
      'y': '0',
      'width': '${12 * factor}',
      'height': '${6 * factor}',
      'count': '1',
      'gap': '0',
    }.entries) {
      fields[e.key] = TextEditingController(text: e.value);
    }
  }

  @override
  void dispose() {
    for (final f in fields.values) {
      f.dispose();
    }
    super.dispose();
  }

  void invalidate() {
    token = null;
    message =
        'Draft fields changed. Add / update the piece or Preview layout before Apply.';
  }

  Future<void> preview(String action, {Json? envelope}) async {
    setState(() {
      working = true;
      token = null;
    });
    try {
      var draft = areas.map((a) => Map<String, dynamic>.from(a)).toList();
      final params = <String, dynamic>{
        'plate_number': plate['number'],
        'envelope':
            envelope ?? {'width': plate['width'], 'height': plate['height']},
      };
      if (action == 'pack') params['pack_direction'] = direction;
      if (action == 'auto') {
        final values = <String, dynamic>{
          'name': fields['name']!.text.trim(),
          'direction': direction,
        };
        for (final key in ['width', 'height']) {
          final value = double.tryParse(fields[key]!.text);
          if (value == null || !value.isFinite || value <= 0) {
            throw FormatException('Enter a positive $key.');
          }
          values[key] = value;
        }
        params['auto_place'] = values;
      }
      if (action == 'remove') {
        draft.removeWhere((a) => a['id'] == selected);
      }
      if (action == 'add' || action == 'update') {
        final values = <String, dynamic>{'name': fields['name']!.text.trim()};
        for (final key in ['x', 'y', 'width', 'height', 'gap']) {
          final v = double.tryParse(fields[key]!.text);
          if (v == null || !v.isFinite) {
            throw FormatException('Enter a finite $key.');
          }
          values[key] = v;
        }
        if (action == 'add') {
          final count = int.tryParse(fields['count']!.text);
          if (count == null || count < 1 || count > 200) {
            throw const FormatException(
              'Use a whole repeat count from 1 to 200.',
            );
          }
          params['repeat'] = {
            ...values,
            'count': count,
            'direction': direction,
          };
        } else {
          draft = [
            for (final a in draft)
              if (a['id'] == selected) {...a, ...values} else a,
          ];
        }
      }
      params['areas'] = draft;
      final r = await m.request('design.patchwork_preview', params);
      if (!mounted) return;
      setState(() {
        if (r['ok'] != true) {
          message = r['error'].toString();
          return;
        }
        final result = r['preview'] as Json;
        areas = (result['areas'] as List).cast<Json>();
        if (result['plate'] != null) plate = result['plate'] as Json;
        token = result['token'] as String;
        if (action == 'remove') selected = null;
        if (action == 'pack' && selected != null) {
          final item = areas.where((a) => a['id'] == selected).firstOrNull;
          if (item != null) {
            for (final key in ['name', 'x', 'y', 'width', 'height']) {
              fields[key]!.text = '${item[key]}';
            }
          }
        }
        message =
            '${areas.length} cut area(s) validated. '
            '${result['envelopeChanged'] == true ? 'Plate size changes on Apply. ' : ''}'
            'Apply saves the envelope and pieces as one undo step. Drawing geometry stays in place.';
      });
    } catch (e) {
      if (mounted) setState(() => message = e.toString());
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  Future<void> apply() async {
    setState(() => working = true);
    final ok = await m.run('design.patchwork_apply', {'token': token});
    if (!mounted) return;
    if (ok) {
      Navigator.pop(context);
      return;
    }
    setState(() {
      working = false;
      token = null;
      message = m.message;
    });
  }

  Json outline(Json a) => {
    'kind': 'rectangle',
    'corner_a': {'x': a['x'], 'y': a['y']},
    'corner_b': {
      'x': number(a['x']) + number(a['width']),
      'y': number(a['y']) + number(a['height']),
    },
    'bounds': [
      a['x'],
      a['y'],
      number(a['x']) + number(a['width']),
      number(a['y']) + number(a['height']),
    ],
  };
  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !working,
    child: AlertDialog(
      title: Text('Patchwork · Plate ${plate['number']}'),
      content: SizedBox(
        width: 860,
        height: math.min(550, MediaQuery.sizeOf(context).height - 180),
        child: SingleChildScrollView(
          child: Column(
            children: [
              Text(
                'Physical drop material inside a ${plate['width']} × ${plate['height']} ${m.units} plate. Guides stay out of geometry and DXF. Auto nest uses these pieces and leaves gaps empty.',
              ),
              Wrap(
                spacing: 8,
                children: [
                  TextButton(
                    onPressed: working || !packingAvailable
                        ? null
                        : () {
                            final factor = m.units == 'mm' ? 25.4 : 1.0;
                            preview(
                              'envelope',
                              envelope: {
                                'width': 120 * factor,
                                'height': 62 * factor,
                              },
                            );
                          },
                    child: const Text('Use 120 × 62 in table'),
                  ),
                  TextButton(
                    onPressed:
                        working ||
                            !packingAvailable ||
                            (plate['width'] == originalPlate['width'] &&
                                plate['height'] == originalPlate['height'])
                        ? null
                        : () => preview(
                            'envelope',
                            envelope: {
                              'width': originalPlate['width'],
                              'height': originalPlate['height'],
                            },
                          ),
                    child: const Text('Restore opening plate size'),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              if (!packingAvailable)
                const Text(
                  'Restart the development app to enable automatic packing and table-envelope controls.',
                ),
              Wrap(
                spacing: 12,
                runSpacing: 8,
                children: [
                  for (final key in fields.keys)
                    SizedBox(
                      width: key == 'name' ? 170 : 88,
                      child: TextField(
                        controller: fields[key],
                        enabled: !working,
                        decoration: InputDecoration(
                          labelText: key == 'name'
                              ? 'Name'
                              : key == 'count'
                              ? 'Count'
                              : '$key (${m.units})',
                        ),
                        onChanged: (_) => setState(invalidate),
                      ),
                    ),
                ],
              ),
              Wrap(
                spacing: 8,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  const Text('Placement direction'),
                  DropdownButton<String>(
                    value: direction,
                    items: const [
                      DropdownMenuItem(value: 'x', child: Text('X')),
                      DropdownMenuItem(value: 'y', child: Text('Y')),
                    ],
                    onChanged: working
                        ? null
                        : (v) => setState(() {
                            direction = v!;
                            invalidate();
                          }),
                  ),
                  TextButton(
                    onPressed: working || !packingAvailable
                        ? null
                        : () => preview('auto'),
                    child: const Text('Auto-place piece'),
                  ),
                  TextButton(
                    onPressed: working ? null : () => preview('add'),
                    child: const Text('Add pieces'),
                  ),
                  TextButton(
                    onPressed: working || selected == null
                        ? null
                        : () => preview('update'),
                    child: const Text('Update selected'),
                  ),
                  TextButton(
                    onPressed: working || selected == null
                        ? null
                        : () => preview('remove'),
                    child: const Text('Remove selected'),
                  ),
                  TextButton(
                    onPressed: working || !packingAvailable || areas.isEmpty
                        ? null
                        : () => preview('pack'),
                    child: const Text('Pack all pieces'),
                  ),
                  TextButton(
                    onPressed: working ? null : () => preview('layout'),
                    child: const Text('Preview layout'),
                  ),
                ],
              ),
              const Text(
                'Auto-place adds one piece using its name and size. Add pieces uses X/Y, count and gap. Pack all uses list order, with no rotation or added gaps; it does not move the drawing.',
              ),
              Text(message),
              SizedBox(
                height: 230,
                child: Row(
                  children: [
                    Expanded(
                      child: ListView(
                        children: [
                          for (final a in areas)
                            ListTile(
                              selected: selected == a['id'],
                              title: Text('${a['name']}'),
                              subtitle: Text(
                                'X ${a['x']} · Y ${a['y']} · ${a['width']} × ${a['height']} ${m.units}',
                              ),
                              onTap: working
                                  ? null
                                  : () => setState(() {
                                      selected = a['id'] as String;
                                      for (final key in [
                                        'name',
                                        'x',
                                        'y',
                                        'width',
                                        'height',
                                      ]) {
                                        fields[key]!.text = '${a[key]}';
                                      }
                                    }),
                            ),
                        ],
                      ),
                    ),
                    Expanded(
                      child: CustomPaint(
                        size: Size.infinite,
                        painter: ShapePreviewPainter(
                          [
                            outline({...plate, 'x': 0, 'y': 0}),
                          ],
                          areas.map(outline).toList(),
                          highlight: areas
                              .where((a) => a['id'] == selected)
                              .map(outline)
                              .toList(),
                        ),
                      ),
                    ),
                  ],
                ),
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
          onPressed: working || token == null ? null : apply,
          child: const Text('Apply layout'),
        ),
      ],
    ),
  );
}
