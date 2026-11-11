import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'controller.dart';
import 'shape_tools_dialog.dart';

class PushFitDialog extends StatefulWidget {
  const PushFitDialog({super.key, required this.model});
  final DesignController model;
  @override
  State<PushFitDialog> createState() => _PushFitDialogState();
}

class _PushFitDialogState extends State<PushFitDialog> {
  DesignController get m => widget.model;
  late final List<Json> sources;
  final roles = <String, String>{};
  late final TextEditingController cutter;
  final clearance = TextEditingController();
  Json? preview, active;
  bool working = false, reviewing = false, zoom = false;
  String message =
      'Assign each source explicitly. Colors do not determine its role.';
  List<Json> list(String key) => (preview?[key] as List? ?? []).cast<Json>();
  @override
  void initState() {
    super.initState();
    cutter = TextEditingController(text: m.units == 'mm' ? '6.35' : '0.25');
    sources = m.selectable
        .where(
          (e) =>
              (['circle', 'rectangle'].contains(e['kind']) ||
                  e['kind'] == 'polyline' && e['closed'] == true) &&
              e['metadata']?['reference_only'] != true &&
              e['metadata']?['mounting_generated'] != true,
        )
        .toList();
  }

  @override
  void dispose() {
    cutter.dispose();
    clearance.dispose();
    super.dispose();
  }

  void invalidate() {
    preview = null;
    active = null;
    reviewing = false;
    zoom = false;
    message = 'Sources or settings changed. Analyze fit before applying.';
  }

  Future<void> analyze([List<String>? included]) async {
    final old = preview;
    setState(() {
      working = true;
      preview = null;
    });
    try {
      final params = <String, dynamic>{};
      if (included == null) {
        for (final entry in {
          'cutter_diameter': cutter,
          'fit_clearance': clearance,
        }.entries) {
          final value = double.tryParse(entry.value.text.trim());
          if (value == null || !value.isFinite) {
            throw FormatException(
              'Enter a finite ${entry.key.replaceAll('_', ' ')}. Clearance is not guessed.',
            );
          }
          params[entry.key] = value;
        }
        params['metal_ids'] = roles.keys
            .where((id) => roles[id] == 'metal')
            .toList();
        params['acrylic_ids'] = roles.keys
            .where((id) => roles[id] == 'acrylic')
            .toList();
      } else {
        params.addAll({'token': old!['token'], 'included_ids': included});
      }
      final response = await m.request(
        included == null ? 'design.push_fit_analyze' : 'design.push_fit_review',
        params,
      );
      if (!mounted) return;
      setState(() {
        if (response['ok'] != true) {
          message = response['error'].toString();
          return;
        }
        preview = response['preview'] as Json;
        reviewing = true;
        active =
            list(
              'candidates',
            ).where((c) => c['id'] == active?['id']).firstOrNull ??
            list('candidates').firstOrNull;
        message =
            '${list('candidates').length} interference region(s); ${(preview!['included'] as List).length} included. '
            '${preview!['removedCount']} generated dependent(s) removed on Apply. '
            '${(preview!['warnings'] as List).join(' ')}';
        if (list('candidates').isEmpty) {
          message = 'No interference regions found. No correction is needed.';
        }
      });
    } catch (e) {
      if (mounted) setState(() => message = e.toString());
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  Future<void> apply() async {
    setState(() => working = true);
    final ok = await m.run('design.push_fit_apply', {
      'token': preview!['token'],
      'review_token': preview!['reviewToken'],
    });
    if (!mounted) return;
    if (ok) {
      Navigator.pop(context);
      return;
    }
    setState(() {
      working = false;
      preview = null;
      message = m.message;
    });
  }

  @override
  Widget build(BuildContext context) {
    final included = (preview?['included'] as List? ?? []).cast<String>();
    final activeBox = bounds(active?['bounds']);
    final zoomBox = activeBox?.inflate(
      math.max(math.max(activeBox.width, activeBox.height) * .5, .001),
    );
    return PopScope(
      canPop: !working,
      child: AlertDialog(
        title: const Text('Push-through fit'),
        content: SizedBox(
          width: 940,
          height: math.min(590, MediaQuery.sizeOf(context).height - 180),
          child: Column(
            children: [
              const Text(
                'Review local metal-opening relief for routed acrylic. Apply replaces the metal only; acrylic stays unchanged. Undo restores the drawing.',
              ),
              const SizedBox(height: 10),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: cutter,
                      enabled: !working,
                      decoration: InputDecoration(
                        labelText: 'Cutter diameter (${m.units})',
                      ),
                      onChanged: (_) => setState(invalidate),
                    ),
                  ),
                  const SizedBox(width: 16),
                  Expanded(
                    child: TextField(
                      controller: clearance,
                      enabled: !working,
                      decoration: InputDecoration(
                        labelText: 'Clearance per side (${m.units})',
                      ),
                      onChanged: (_) => setState(invalidate),
                    ),
                  ),
                  const SizedBox(width: 16),
                  FilledButton(
                    onPressed: working ? null : () => analyze(),
                    child: const Text('Analyze fit'),
                  ),
                ],
              ),
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Text(message),
              ),
              Row(
                children: [
                  TextButton(
                    onPressed: working
                        ? null
                        : () => setState(() => reviewing = false),
                    child: const Text('Assign sources'),
                  ),
                  TextButton(
                    onPressed: working || preview == null
                        ? null
                        : () => setState(() => reviewing = true),
                    child: const Text('Review regions'),
                  ),
                  const Spacer(),
                  if (reviewing)
                    TextButton(
                      onPressed: working || active == null
                          ? null
                          : () => setState(() => zoom = !zoom),
                      child: Text(zoom ? 'Show whole fit' : 'Zoom to region'),
                    ),
                ],
              ),
              Expanded(
                child: Row(
                  children: [
                    Expanded(
                      child: reviewing
                          ? ListView(
                              children: [
                                TextButton(
                                  onPressed: working
                                      ? null
                                      : () => analyze(
                                          list('candidates')
                                              .where(
                                                (c) => c['recommended'] == true,
                                              )
                                              .map((c) => c['id'] as String)
                                              .toList(),
                                        ),
                                  child: const Text('Use recommended'),
                                ),
                                for (final (index, c) in list(
                                  'candidates',
                                ).indexed)
                                  ListTile(
                                    selected: active?['id'] == c['id'],
                                    onTap: () => setState(() => active = c),
                                    leading: Checkbox(
                                      value: included.contains(c['id']),
                                      onChanged: working
                                          ? null
                                          : (value) => analyze(
                                              value == true
                                                  ? [
                                                      ...included,
                                                      c['id'] as String,
                                                    ]
                                                  : included
                                                        .where(
                                                          (id) => id != c['id'],
                                                        )
                                                        .toList(),
                                            ),
                                    ),
                                    title: Text(
                                      'Region ${index + 1} · ${c['recommended'] == true ? 'Local relief' : 'Broad mismatch'}',
                                    ),
                                    subtitle: Text(
                                      '${c['note']}\nArea ${number(c['area']).toStringAsPrecision(5)} ${m.units}²',
                                    ),
                                  ),
                              ],
                            )
                          : Column(
                              children: [
                                Wrap(
                                  children: [
                                    for (final role in ['metal', 'acrylic'])
                                      TextButton(
                                        onPressed:
                                            working || m.selection.isEmpty
                                            ? null
                                            : () => setState(() {
                                                invalidate();
                                                for (final e in sources.where(
                                                  (e) => m.selection.contains(
                                                    e['id'],
                                                  ),
                                                )) {
                                                  roles[e['id'] as String] =
                                                      role;
                                                }
                                              }),
                                        child: Text('Selection → $role'),
                                      ),
                                  ],
                                ),
                                Text(
                                  '${roles.values.where((r) => r == 'metal').length} metal · ${roles.values.where((r) => r == 'acrylic').length} acrylic',
                                ),
                                Expanded(
                                  child: ListView(
                                    children: [
                                      if (sources.isEmpty)
                                        const Text(
                                          'Draw or import closed contours on visible, unlocked layers first.',
                                        ),
                                      for (final (index, e) in sources.indexed)
                                        ListTile(
                                          selected: active?['id'] == e['id'],
                                          onTap: () =>
                                              setState(() => active = e),
                                          title: Text(
                                            '${index + 1}. ${e['metadata']?['name'] ?? e['kind']} · ${e['layer'] ?? '0'}',
                                          ),
                                          subtitle: Text(
                                            'At ${bounds(e['bounds'])?.center.dx.toStringAsFixed(3)}, ${bounds(e['bounds'])?.center.dy.toStringAsFixed(3)} ${m.units}',
                                          ),
                                          trailing: DropdownButton<String>(
                                            value: roles[e['id']] ?? '',
                                            items: const [
                                              DropdownMenuItem(
                                                value: '',
                                                child: Text('Unused'),
                                              ),
                                              DropdownMenuItem(
                                                value: 'metal',
                                                child: Text('Metal'),
                                              ),
                                              DropdownMenuItem(
                                                value: 'acrylic',
                                                child: Text('Acrylic'),
                                              ),
                                            ],
                                            onChanged: working
                                                ? null
                                                : (value) => setState(() {
                                                    invalidate();
                                                    roles[e['id'] as String] =
                                                        value!;
                                                    active = e;
                                                  }),
                                          ),
                                        ),
                                    ],
                                  ),
                                ),
                              ],
                            ),
                    ),
                    const VerticalDivider(),
                    Expanded(
                      child: Column(
                        children: [
                          const Text(
                            'Gray: source · Blue: metal result\nGreen: routed acrylic approximation · Orange: focused region/source',
                            textAlign: TextAlign.center,
                          ),
                          Expanded(
                            child: CustomPaint(
                              size: Size.infinite,
                              painter: ShapePreviewPainter(
                                preview == null ? sources : list('sources'),
                                list('entities'),
                                acrylic: list('acrylic'),
                                highlight: active == null
                                    ? []
                                    : reviewing
                                    ? (active!['entities'] as List? ?? [])
                                          .cast<Json>()
                                    : [active!],
                                zoomBounds: reviewing && zoom ? zoomBox : null,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: working ? null : () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: working || preview == null || included.isEmpty
                ? null
                : apply,
            child: const Text('Apply metal corrections'),
          ),
        ],
      ),
    );
  }
}
