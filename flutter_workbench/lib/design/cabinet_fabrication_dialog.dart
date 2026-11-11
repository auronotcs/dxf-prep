import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'controller.dart';
import 'cabinet_cutter_dialog.dart';
import 'canvas.dart' show entityPath;

class CabinetFabricationDialog extends StatefulWidget {
  const CabinetFabricationDialog({
    super.key,
    required this.model,
    required this.token,
  });
  final DesignController model;
  final String token;
  @override
  State<CabinetFabricationDialog> createState() =>
      _CabinetFabricationDialogState();
}

class _CabinetFabricationDialogState extends State<CabinetFabricationDialog> {
  DesignController get m => widget.model;
  late final Object? revision;
  Json? data, report, highlight, privateTool, published;
  bool get supportsJobs => m.state['cabinetFabricationJobs'] == true;
  String? error;
  String top = '', view = 'plan';
  int station = -1;
  final openings = <String>{};
  final fields = <String, TextEditingController>{
    for (final k in ['top_thickness', 'edge_clearance', 'minimum_web'])
      k: TextEditingController(),
  };
  final camera = TransformationController();
  final previewAnchor = GlobalKey();
  bool busy = false, stale = false, registration = false, fit = false;
  @override
  void initState() {
    super.initState();
    revision = m.state['revision'];
    m.addListener(changed);
    begin();
  }

  @override
  void dispose() {
    m.removeListener(changed);
    for (final c in fields.values) {
      c.dispose();
    }
    camera.dispose();
    super.dispose();
  }

  void changed() {
    if (m.state['revision'] != revision || !m.ready) {
      setState(() {
        stale = true;
        report = null;
        highlight = null;
        error =
            'Document changed. Close this inspection and preview Cabinet Tabs again.';
      });
    }
  }

  void edit(VoidCallback f) => setState(() {
    f();
    report = null;
    view = 'plan';
    published = null;
    highlight = null;
    registration = false;
    fit = false;
    error = null;
    camera.value = Matrix4.identity();
  });
  Future<void> begin() async {
    setState(() => busy = true);
    try {
      final r = await m.request('design.cabinet_fabrication_begin', {
        'token': widget.token,
      });
      if (!mounted) return;
      if (r['ok'] != true) {
        throw StateError('${r['error']}');
      }
      if (stale) return;
      setState(() {
        data = r['review'] as Json;
        final s = data!['settings'] as Json;
        top = s['top_id'];
        station = s['tool_station'];
        openings.addAll((s['keepout_ids'] as List).cast<String>());
        for (final k in fields.keys) {
          fields[k]!.text = '${s[k]}';
        }
      });
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> inspect() async {
    setState(() {
      busy = true;
      view = 'plan';
      published = null;
      report = null;
      highlight = null;
      error = null;
      camera.value = Matrix4.identity();
    });
    try {
      final values = <String, double>{};
      for (final e in fields.entries) {
        final v = double.tryParse(e.value.text);
        if (v == null || !v.isFinite) {
          throw StateError('Enter finite physical dimensions in ${m.units}.');
        }
        values[e.key] = v;
      }
      final r = await m.request(
        supportsJobs
            ? 'design.cabinet_fabrication_jobs_preview'
            : 'design.cabinet_fabrication_inspect',
        {
          if (supportsJobs) 'private_tool': privateTool?['reference'],
          'token': widget.token,
          'settings': {
            'top_id': top,
            'keepout_ids': openings.toList(),
            'tool_station': station,
            ...values,
            'registration_reviewed': registration,
            'fit_reviewed': fit,
          },
        },
      );
      if (!mounted) return;
      if (r['ok'] != true) {
        throw StateError('${r['error']}');
      }
      if (!stale) setState(() => report = r['review'] as Json);
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> chooseCutter() async {
    final result = await showDialog<Json>(
      context: context,
      builder: (_) => CabinetCutterDialog(model: m),
    );
    if (mounted && !stale && result != null) {
      edit(() {
        privateTool = result;
        station = result['tool']['station'] as int;
      });
    }
  }

  Future<void> saveJobs() async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final result = await m.request('design.cabinet_fabrication_export', {
        'token': widget.token,
        'review_token': report!['review_token'],
      });
      if (result['ok'] != true) throw StateError('${result['error']}');
      if (mounted && !stale) {
        setState(() => published = result['review'] as Json);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          error = '$e';
          report = null;
        });
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  void locate(Json issue) {
    setState(() {
      view = issue['view'] == 'back' ? 'back' : 'plan';
      highlight = issue;
      camera.value = Matrix4.identity();
    });
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final target = previewAnchor.currentContext;
      if (mounted && target != null) {
        Scrollable.ensureVisible(
          target,
          alignment: .2,
          duration: const Duration(milliseconds: 180),
        );
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final active = !busy && !stale;
    final candidates = (data?['candidates'] as List? ?? []).cast<Json>();
    final tools = [
      for (final t in (data?['tools'] as List? ?? []).cast<Json>())
        if (privateTool == null ||
            t['station'] != privateTool!['tool']['station'])
          t,
      if (privateTool != null) privateTool!['tool'] as Json,
    ];
    final known = {for (final e in candidates) e['id'] as String};
    return PopScope(
      canPop: !busy,
      child: AlertDialog(
        title: const Text('Cabinet fabrication inspection'),
        content: SizedBox(
          width: 1000,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Inspect the current tab layout. Preview and save independent machining copies while preserving the assembly drawing.',
                ),
                if (busy) const LinearProgressIndicator(),
                if (error != null)
                  Text(
                    error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                if (published != null)
                  SelectableText(
                    'Saved independent jobs and assembly review:\n${(published!['paths'] as List).join('\n')}\n${published!['assembly_path']}',
                  ),
                if (data != null) ...[
                  if (data!['warning'] != null) Text('${data!['warning']}'),
                  DropdownButtonFormField<String>(
                    key: ValueKey('top-$top'),
                    initialValue: top,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Actual cabinet-top perimeter',
                    ),
                    items: [
                      const DropdownMenuItem(
                        value: '',
                        child: Text('Choose the actual top'),
                      ),
                      for (final e in candidates)
                        DropdownMenuItem(
                          value: e['id'] as String,
                          child: Text(
                            '${e['label']} · ${e['layer']} (${e['kind']})',
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      if (top.isNotEmpty && !known.contains(top))
                        DropdownMenuItem(
                          value: top,
                          child: Text('Unavailable top: $top'),
                        ),
                    ],
                    onChanged: active ? (v) => edit(() => top = v!) : null,
                  ),
                  const SizedBox(height: 8),
                  DropdownButtonFormField<int>(
                    key: ValueKey('tool-$station'),
                    initialValue: station,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Loaded cutter for slot cleanout',
                    ),
                    items: [
                      const DropdownMenuItem(
                        value: -1,
                        child: Text('Choose a loaded flat end mill'),
                      ),
                      for (final t in tools)
                        DropdownMenuItem(
                          value: t['station'] as int,
                          child: Text(
                            'T${t['station']} · ${t['name']} · Ø ${t['diameter']} ${data!['units']}',
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      if (station != -1 &&
                          !tools.any((t) => t['station'] == station))
                        DropdownMenuItem(
                          value: station,
                          child: Text('Unavailable station T$station'),
                        ),
                    ],
                    onChanged: active
                        ? (v) => edit(() {
                            station = v!;
                            if (privateTool != null &&
                                privateTool!['tool']['station'] != station) {
                              privateTool = null;
                            }
                          })
                        : null,
                  ),
                  if (supportsJobs)
                    TextButton.icon(
                      onPressed: active ? chooseCutter : null,
                      icon: const Icon(Icons.library_books_outlined),
                      label: const Text('Choose private library cutter...'),
                    ),
                  if (privateTool != null)
                    Text(
                      'Private cutter T$station replaces that station only in exported copies.',
                    ),
                  if (tools.isEmpty)
                    const Text(
                      'No flat end mill is loaded. Choose a private library cutter for this fabrication preview.',
                    ),
                  const SizedBox(height: 8),
                  Text(
                    'Protected openings (${openings.length}) — select all existing openings to retain in the top.',
                  ),
                  ConstrainedBox(
                    constraints: const BoxConstraints(maxHeight: 140),
                    child: SingleChildScrollView(
                      child: Column(
                        children: [
                          for (final e in candidates.where(
                            (e) => e['id'] != top,
                          ))
                            CheckboxListTile(
                              dense: true,
                              contentPadding: EdgeInsets.zero,
                              value: openings.contains(e['id']),
                              title: Text('${e['label']} · ${e['layer']}'),
                              onChanged: active
                                  ? (v) => edit(() {
                                      if (v == true) {
                                        openings.add(e['id']);
                                      } else {
                                        openings.remove(e['id']);
                                      }
                                    })
                                  : null,
                            ),
                          for (final id in openings.where(
                            (id) => !known.contains(id) || id == top,
                          ))
                            CheckboxListTile(
                              dense: true,
                              contentPadding: EdgeInsets.zero,
                              value: true,
                              title: Text('Review invalid opening: $id'),
                              onChanged: active
                                  ? (_) => edit(() => openings.remove(id))
                                  : null,
                            ),
                        ],
                      ),
                    ),
                  ),
                  Wrap(
                    spacing: 12,
                    runSpacing: 8,
                    children: [
                      for (final e in const {
                        'top_thickness': 'Cabinet-top thickness',
                        'edge_clearance': 'Extra edge clearance',
                        'minimum_web': 'Minimum slot/opening web',
                      }.entries)
                        SizedBox(
                          width: 230,
                          child: TextField(
                            controller: fields[e.key],
                            enabled: active,
                            decoration: InputDecoration(
                              labelText: e.value,
                              suffixText: data!['units'],
                            ),
                            onChanged: (_) => edit(() {}),
                          ),
                        ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Skin reserve ${data!['skin']} ${data!['units']} plus extra clearance applies around the entire top. Total width play ${data!['width_play']}; total thickness play ${data!['thickness_play']}. Rounded slot ends extend by ${(number(data!['slot_thickness']) / 2).toStringAsFixed(4)} ${data!['units']} at each end.',
                  ),
                  const Text(
                    'Test fit on a coupon. These geometry checks do not verify weld strength or physical assembly measurements.',
                  ),
                  CheckboxListTile(
                    contentPadding: EdgeInsets.zero,
                    value: registration,
                    title: const Text(
                      'I reviewed the actual top, registration and skin envelope',
                    ),
                    onChanged: active
                        ? (v) => setState(() {
                            registration = v == true;
                            report = null;
                            published = null;
                            highlight = null;
                          })
                        : null,
                  ),
                  CheckboxListTile(
                    contentPadding: EdgeInsets.zero,
                    value: fit,
                    title: const Text(
                      'I reviewed total fit play and extended slot ends',
                    ),
                    onChanged: active
                        ? (v) => setState(() {
                            fit = v == true;
                            report = null;
                            published = null;
                            highlight = null;
                          })
                        : null,
                  ),
                ],
                if (report != null) ...[
                  Text(
                    report!['geometry_checks_passed'] == true
                        ? (report!['can_export'] == true
                              ? 'Geometry checks passed. Review both job previews, then save the pair.'
                              : 'Geometry checks passed for these inputs. Machining-job creation is not enabled here.')
                        : 'Resolve the findings below; no machining permission is granted.',
                  ),
                  Text('${report!['message'] ?? ''}'),
                  for (final job
                      in (report!['jobs'] as List? ?? []).cast<Json>())
                    Text(
                      '${job['name']} · ${job['objects']} contours · thickness ${job['thickness']} ${data!['units']} · bounds ${job['bounds']}',
                    ),
                  Wrap(
                    spacing: 8,
                    children: [
                      for (final e in {
                        'plan': 'Top / actual slots',
                        'back': 'Tabbed backs',
                        if (report!['jobs'] != null)
                          'job0': 'Back job placement',
                        if (report!['jobs'] != null)
                          'job1': 'Top job placement',
                      }.entries)
                        ChoiceChip(
                          label: Text(e.value),
                          selected: view == e.key,
                          onSelected: (_) => setState(() {
                            view = e.key;
                            highlight = null;
                            camera.value = Matrix4.identity();
                          }),
                        ),
                      TextButton(
                        onPressed: () => setState(() {
                          highlight = null;
                          camera.value = Matrix4.identity();
                        }),
                        child: const Text('Fit preview'),
                      ),
                    ],
                  ),
                  const Text(
                    'Gray: top/source · Green: skin reserve / backs · Red: protected openings · Orange: actual slot relief',
                  ),
                  LayoutBuilder(
                    builder: (context, c) => SizedBox(
                      key: previewAnchor,
                      height: 320,
                      child: InteractiveViewer(
                        transformationController: camera,
                        minScale: .5,
                        maxScale: 20,
                        child: SizedBox(
                          width: c.maxWidth,
                          height: 320,
                          child: CustomPaint(
                            key: const ValueKey('fabrication-preview'),
                            painter: FabricationPainter(
                              (report!['views'][view] as List).cast<Json>(),
                              highlight,
                              number(report!['focus_span']),
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                  for (final issue in (report!['issues'] as List).cast<Json>())
                    ListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      title: Text(
                        '${issue['severity'].toString().toUpperCase()} · ${issue['code']}',
                      ),
                      subtitle: Text('${issue['message']}'),
                      trailing:
                          issue['point'] != null ||
                              (issue['entity_ids'] as List).isNotEmpty
                          ? TextButton(
                              onPressed: () => locate(issue),
                              child: const Text('Locate'),
                            )
                          : null,
                    ),
                  if (highlight != null)
                    Text(
                      'Located: ${highlight!['code']} · ${highlight!['message']}',
                      key: const ValueKey('located-finding'),
                    ),
                ],
              ],
            ),
          ),
        ),
        actions: [
          if (published != null)
            TextButton(
              onPressed: active
                  ? () => Navigator.pop(
                      context,
                      (published!['paths'] as List).cast<String>(),
                    )
                  : null,
              child: const Text('Open machining jobs'),
            ),
          if (supportsJobs && published == null)
            OutlinedButton(
              onPressed: active && report?['can_export'] == true
                  ? saveJobs
                  : null,
              child: const Text('Save machining pair'),
            ),
          TextButton(
            onPressed: busy ? null : () => Navigator.pop(context),
            child: const Text('Back to tab layout'),
          ),
          FilledButton(
            onPressed: active && data != null ? inspect : null,
            child: const Text('Check & Preview'),
          ),
        ],
      ),
    );
  }
}

class FabricationPainter extends CustomPainter {
  FabricationPainter(this.entities, this.issue, this.span);
  final List<Json> entities;
  final Json? issue;
  final double span;
  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    canvas.clipRect(Offset.zero & size);
    canvas.drawColor(Colors.white, BlendMode.src);
    final paths = entities.map(entityPath).toList();
    Rect? bounds;
    final focus = issue?['point'] as Json?;
    if (focus != null) {
      bounds = Rect.fromCenter(center: point(focus), width: span, height: span);
    } else {
      for (final p in paths) {
        bounds = bounds == null
            ? p.getBounds()
            : bounds.expandToInclude(p.getBounds());
      }
    }
    if (bounds == null) {
      canvas.restore();
      return;
    }
    final scale = math.min(
      (size.width - 30) / math.max(bounds.width, 1e-6),
      (size.height - 30) / math.max(bounds.height, 1e-6),
    );
    final matrix = Matrix4.identity()
      ..translateByDouble(
        size.width / 2 - bounds.center.dx * scale,
        size.height / 2 + bounds.center.dy * scale,
        0,
        1,
      )
      ..scaleByDouble(scale, -scale, 1, 1);
    final ids = (issue?['entity_ids'] as List? ?? []).cast<String>();
    final pair = issue?['pair_id'];
    for (var i = 0; i < paths.length; i++) {
      final e = entities[i], meta = entities[i]['metadata'] as Json? ?? {};
      final hit =
          ids.contains(e['id']) ||
          ids.contains(meta['mounting_owner_entity_id']) ||
          (pair != null &&
              pair != '' &&
              (meta['cabinet_pair_id'] == pair ||
                  meta['tab_slot_pair_id'] == pair));
      final color = hit
          ? Colors.red
          : switch (e['review_role']) {
              'slot' => Colors.orange.shade800,
              'opening' => Colors.red.shade800,
              'reserve' => Colors.green.shade300,
              'back' => Colors.teal,
              _ => Colors.blueGrey,
            };
      canvas.drawPath(
        paths[i].transform(matrix.storage),
        Paint()
          ..color = color
          ..style = PaintingStyle.stroke
          ..strokeWidth = hit ? 3 : 1.5,
      );
    }
    if (focus != null) {
      canvas.drawCircle(
        Offset(size.width / 2, size.height / 2),
        8,
        Paint()
          ..color = Colors.red
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2,
      );
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(covariant FabricationPainter old) => true;
}
