import 'dart:convert';
import 'package:flutter/material.dart';
import 'controller.dart';
import 'cabinet_fabrication_dialog.dart';
import 'assembly_review_dialog.dart' show AssemblyPreviewPainter;

class CabinetAuthoringDialog extends StatefulWidget {
  const CabinetAuthoringDialog({super.key, required this.model});
  final DesignController model;
  @override
  State<CabinetAuthoringDialog> createState() => _CabinetAuthoringDialogState();
}

class _CabinetAuthoringDialogState extends State<CabinetAuthoringDialog> {
  DesignController get m => widget.model;
  late final Object? revision;
  Json? data, plan, preview;
  String? error;
  String saved = '';
  int epoch = 0, serial = 0;
  bool busy = false, stale = false, applying = false, reviewed = false;
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
    super.dispose();
  }

  void changed() {
    if (!applying && (m.state['revision'] != revision || !m.ready)) {
      setState(() {
        stale = true;
        preview = null;
        error = 'Document changed. Reopen Cabinet Tabs authoring.';
      });
    }
  }

  Json clone(Json value) => jsonDecode(jsonEncode(value)) as Json;
  Future<void> begin() async {
    setState(() => busy = true);
    try {
      final r = await m.request('design.cabinet_begin');
      if (!mounted) return;
      if (r['ok'] != true) throw StateError('${r['error']}');
      setState(() {
        data = r['context'] as Json;
        load('');
      });
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  void load(String id) {
    saved = id;
    epoch++;
    preview = null;
    reviewed = false;
    error = null;
    plan = id.isEmpty
        ? {
            'schema': 'cabinet-tab-plan-v1',
            'plan_id': data!['new_id'],
            'units': data!['units'],
            ...clone(data!['defaults'] as Json),
            'planes': <Json>[],
            'tabs': <Json>[],
          }
        : clone((data!['saved'] as Json)[id] as Json);
  }

  void edit(VoidCallback f) => setState(() {
    f();
    preview = null;
    reviewed = false;
    error = null;
  });
  Json numericPlan() {
    final p = clone(plan!);
    void values(Json row, List<String> keys) {
      for (final k in keys) {
        final v = double.tryParse('${row[k]}');
        if (v == null || !v.isFinite) {
          throw StateError('Enter finite numeric dimensions and angles.');
        }
        row[k] = v;
      }
    }

    values(p, [
      'back_thickness',
      'front_skin_thickness',
      'width_clearance_total',
      'thickness_clearance_total',
    ]);
    for (final plane in (p['planes'] as List).cast<Json>()) {
      values(plane, [
        'local_u_origin',
        'cabinet_top_v',
        'angle_degrees',
        'inward_setback',
      ]);
      values(plane['plan_origin'] as Json, ['x', 'y']);
    }
    for (final tab in (p['tabs'] as List).cast<Json>()) {
      values(tab, ['center_u', 'width', 'projection']);
    }
    return p;
  }

  Future<void> calculate() async {
    setState(() {
      busy = true;
      preview = null;
      reviewed = false;
      error = null;
    });
    try {
      final r = await m.request('design.cabinet_preview', {
        'context_token': data!['token'],
        'plan': numericPlan(),
      });
      if (!mounted) return;
      if (r['ok'] != true) throw StateError('${r['error']}');
      if (!stale) setState(() => preview = r['preview'] as Json);
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> reregister() async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Accept current source geometry?'),
        content: const Text(
          'Saved dimensions convert if drawing units changed. Moving or rotating source parts does NOT update cabinet origins. Review every datum, origin, angle, inward direction and tab before Apply.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Accept sources for review'),
          ),
        ],
      ),
    );
    if (!mounted || yes != true) return;
    setState(() {
      busy = true;
      preview = null;
      reviewed = false;
      error = null;
    });
    try {
      final r = await m.request('design.cabinet_reregister', {
        'context_token': data!['token'],
        'plan': numericPlan(),
        'confirmed': true,
      });
      if (!mounted) return;
      if (r['ok'] != true) throw StateError('${r['error']}');
      if (!stale) {
        setState(() {
          plan = r['plan'] as Json;
          epoch++;
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> apply() async {
    setState(() {
      busy = true;
      applying = true;
    });
    final ok = await m.run('design.cabinet_apply', {
      'token': preview!['token'],
      'reviewed': reviewed,
    });
    if (!mounted) return;
    if (ok) {
      Navigator.pop(context);
    } else {
      setState(() {
        busy = false;
        applying = false;
        preview = null;
        error = m.message;
      });
    }
  }

  Widget field(
    Json target,
    String key,
    String label,
    String identity, {
    double width = 190,
  }) => SizedBox(
    width: width,
    child: TextFormField(
      key: ValueKey('$epoch-$identity-$key'),
      initialValue: '${target[key]}',
      enabled: !busy && !stale,
      decoration: InputDecoration(labelText: label),
      onChanged: (v) => edit(() => target[key] = v),
    ),
  );
  @override
  Widget build(BuildContext context) {
    final active = !busy && !stale;
    final planes = (plan?['planes'] as List? ?? []).cast<Json>();
    final tabs = (plan?['tabs'] as List? ?? []).cast<Json>();
    return PopScope(
      canPop: !busy,
      child: AlertDialog(
        title: const Text('Cabinet Tabs authoring'),
        content: SizedBox(
          width: 900,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Define flat backs and paired slots using explicit cabinet registration. This editor saves reference-only review geometry. Original backs/holes stay intact; fabrication is a separate workflow.',
                ),
                if (busy) const LinearProgressIndicator(),
                if (error != null)
                  Text(
                    error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                if (data != null && plan != null) ...[
                  DropdownButtonFormField<String>(
                    initialValue: saved,
                    decoration: const InputDecoration(labelText: 'Saved plan'),
                    items: [
                      const DropdownMenuItem(
                        value: '',
                        child: Text('New plan'),
                      ),
                      for (final id in (data!['saved'] as Json).keys)
                        DropdownMenuItem(value: id, child: Text(id)),
                    ],
                    onChanged: active ? (v) => setState(() => load(v!)) : null,
                  ),
                  Text(
                    'Editor units: ${plan!['units']} · Drawing units: ${m.units}. Angles in degrees.',
                  ),
                  Wrap(
                    spacing: 12,
                    runSpacing: 8,
                    children: [
                      field(plan!, 'back_thickness', 'Back thickness', 'stock'),
                      field(
                        plan!,
                        'front_skin_thickness',
                        'Front skin thickness',
                        'stock',
                      ),
                      field(
                        plan!,
                        'width_clearance_total',
                        'Total width clearance',
                        'stock',
                      ),
                      field(
                        plan!,
                        'thickness_clearance_total',
                        'Total thickness clearance',
                        'stock',
                      ),
                    ],
                  ),
                  if (data!['warning'] != null) Text('${data!['warning']}'),
                  Wrap(
                    spacing: 8,
                    children: [
                      OutlinedButton(
                        onPressed:
                            active && (data!['candidates'] as List).isNotEmpty
                            ? () => edit(() {
                                for (final raw
                                    in (data!['candidates'] as List)
                                        .cast<Json>()) {
                                  if (!planes.any(
                                    (p) =>
                                        p['owner_entity_id'] ==
                                        raw['owner_entity_id'],
                                  )) {
                                    (plan!['planes'] as List).add(clone(raw));
                                  }
                                }
                              })
                            : null,
                        child: const Text('Add selected backs and holes'),
                      ),
                      OutlinedButton(
                        onPressed: active && planes.isNotEmpty
                            ? reregister
                            : null,
                        child: const Text('Re-register current sources'),
                      ),
                    ],
                  ),
                  if (planes.isEmpty)
                    const Text(
                      'Select original closed back polylines in the drawing, or load a saved plan. Defaults are not physical registration.',
                    ),
                  for (final plane in planes)
                    Card(
                      child: Padding(
                        padding: const EdgeInsets.all(10),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            SelectableText(
                              'Back ${plane['owner_entity_id']} · members ${(plane['member_ids'] as List).join(', ')}',
                            ),
                            Wrap(
                              spacing: 10,
                              runSpacing: 6,
                              children: [
                                field(
                                  plane,
                                  'local_u_origin',
                                  'Local U datum',
                                  '${plane['owner_entity_id']}',
                                ),
                                field(
                                  plane,
                                  'cabinet_top_v',
                                  'Cabinet top V',
                                  '${plane['owner_entity_id']}',
                                ),
                                field(
                                  plane['plan_origin'] as Json,
                                  'x',
                                  'Cabinet origin X',
                                  '${plane['owner_entity_id']}-origin',
                                ),
                                field(
                                  plane['plan_origin'] as Json,
                                  'y',
                                  'Cabinet origin Y',
                                  '${plane['owner_entity_id']}-origin',
                                ),
                                field(
                                  plane,
                                  'angle_degrees',
                                  'Plane angle',
                                  '${plane['owner_entity_id']}',
                                ),
                                field(
                                  plane,
                                  'inward_setback',
                                  'Inward setback',
                                  '${plane['owner_entity_id']}',
                                ),
                              ],
                            ),
                            for (final tab in tabs.where(
                              (t) =>
                                  t['owner_entity_id'] ==
                                  plane['owner_entity_id'],
                            ))
                              Padding(
                                padding: const EdgeInsets.only(top: 8),
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text('Pair ${tab['pair_id']}'),
                                    Wrap(
                                      spacing: 10,
                                      children: [
                                        field(
                                          tab,
                                          'center_u',
                                          'Tab center U',
                                          '${tab['pair_id']}',
                                        ),
                                        field(
                                          tab,
                                          'width',
                                          'Tab width',
                                          '${tab['pair_id']}',
                                        ),
                                        field(
                                          tab,
                                          'projection',
                                          'Tab projection',
                                          '${tab['pair_id']}',
                                        ),
                                        TextButton(
                                          onPressed: active
                                              ? () => edit(
                                                  () => (plan!['tabs'] as List)
                                                      .remove(tab),
                                                )
                                              : null,
                                          child: const Text('Remove pair'),
                                        ),
                                      ],
                                    ),
                                  ],
                                ),
                              ),
                            Wrap(
                              spacing: 8,
                              children: [
                                TextButton(
                                  onPressed: active
                                      ? () => edit(() {
                                          final f = plan!['units'] == 'mm'
                                              ? 25.4
                                              : 1.0;
                                          (plan!['tabs'] as List).add({
                                            'pair_id':
                                                'pair-${DateTime.now().microsecondsSinceEpoch}-${serial++}',
                                            'owner_entity_id':
                                                plane['owner_entity_id'],
                                            'center_u':
                                                (double.tryParse(
                                                      '${plane['local_u_origin']}',
                                                    ) ??
                                                    0) +
                                                2 * f,
                                            'width': 2 * f,
                                            'projection': .5 * f,
                                          });
                                        })
                                      : null,
                                  child: const Text('Add tab pair'),
                                ),
                                TextButton(
                                  onPressed: active
                                      ? () => edit(() {
                                          (plan!['planes'] as List).remove(
                                            plane,
                                          );
                                          (plan!['tabs'] as List).removeWhere(
                                            (t) =>
                                                t['owner_entity_id'] ==
                                                plane['owner_entity_id'],
                                          );
                                        })
                                      : null,
                                  child: const Text('Remove back from draft'),
                                ),
                              ],
                            ),
                          ],
                        ),
                      ),
                    ),
                  if (preview != null) ...[
                    Text(
                      '${planes.length} backs · ${tabs.length} tab pairs. Purple: flat back review; blue: cabinet slot review.',
                    ),
                    SizedBox(
                      height: 260,
                      width: double.infinity,
                      child: CustomPaint(
                        painter: AssemblyPreviewPainter(
                          (preview!['geometry'] as List).cast<Json>(),
                        ),
                      ),
                    ),
                    CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      value: reviewed,
                      onChanged: active
                          ? (v) => setState(() => reviewed = v == true)
                          : null,
                      title: const Text(
                        'I reviewed every source, datum, plane and inward direction. Save references only.',
                      ),
                    ),
                  ],
                ],
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: busy ? null : () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          OutlinedButton(
            onPressed: active && plan != null ? calculate : null,
            child: const Text('Preview cabinet review'),
          ),
          OutlinedButton(
            onPressed:
                active &&
                    preview != null &&
                    m.state['cabinetFabricationInspection'] == true
                ? () async {
                    final paths = await showDialog<List<String>>(
                      context: context,
                      builder: (_) => CabinetFabricationDialog(
                        model: m,
                        token: preview!['token'],
                      ),
                    );
                    if (context.mounted && paths != null) {
                      Navigator.pop(context, paths);
                    }
                  }
                : null,
            child: const Text('Inspect fabrication...'),
          ),
          FilledButton(
            onPressed: active && preview != null && reviewed ? apply : null,
            child: const Text('Apply cabinet review'),
          ),
        ],
      ),
    );
  }
}
