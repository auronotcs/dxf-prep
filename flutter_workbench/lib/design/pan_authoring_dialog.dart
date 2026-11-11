import 'package:flutter/material.dart';
import 'controller.dart';
import 'assembly_review_dialog.dart' show AssemblyPreviewPainter;

class PanAuthoringDialog extends StatefulWidget {
  const PanAuthoringDialog({super.key, required this.model});
  final DesignController model;
  @override
  State<PanAuthoringDialog> createState() => _PanAuthoringDialogState();
}

class _PanAuthoringDialogState extends State<PanAuthoringDialog> {
  DesignController get m => widget.model;
  late final Object? revision;
  late final factor = m.units == 'mm' ? 25.4 : 1.0;
  late final fields = <String, TextEditingController>{
    for (final e in {
      'thickness': .063,
      'inside_lip': 1.0625,
      'inside_radius': .063,
      'k_factor': .4,
      'cutter_diameter': .25,
      'corner_gap': .063,
      'diameter': .125,
      'max_spacing': 24.0,
      'end_offset': 2.0,
      'mark_offset': 0.0,
    }.entries)
      e.key: TextEditingController(
        text: '${e.value * (e.key == 'k_factor' ? 1 : factor)}',
      ),
  };
  final flags = <String, bool>{
    'enabled': true,
    'top': true,
    'bottom': true,
    'left': true,
    'right': true,
  };
  String mode = 'create';
  bool marks = false,
      reviewed = false,
      busy = false,
      applying = false,
      stale = false;
  Json? preview;
  String? error;
  @override
  void initState() {
    super.initState();
    revision = m.state['revision'];
    m.addListener(changed);
  }

  void changed() {
    if (!applying && (m.state['revision'] != revision || !m.ready)) {
      setState(() {
        stale = true;
        preview = null;
        error = 'Drawing changed. Reopen DR Pans.';
      });
    }
  }

  @override
  void dispose() {
    m.removeListener(changed);
    for (final c in fields.values) {
      c.dispose();
    }
    super.dispose();
  }

  void edit(VoidCallback f) => setState(() {
    f();
    preview = null;
    reviewed = false;
    error = null;
  });
  Future<void> calculate() async {
    final values = <String, double>{};
    for (final e in fields.entries) {
      final n = double.tryParse(e.value.text);
      if (n == null || !n.isFinite) {
        setState(() => error = 'Enter finite numeric dimensions.');
        return;
      }
      values[e.key] = n;
    }
    setState(() {
      busy = true;
      preview = null;
      reviewed = false;
      error = null;
    });
    try {
      final r = await m.request('design.pan_preview', {
        'mode': mode,
        'settings': {
          for (final k in [
            'thickness',
            'inside_lip',
            'inside_radius',
            'k_factor',
            'cutter_diameter',
            'corner_gap',
          ])
            k: values[k],
        },
        'holes': {
          ...flags,
          for (final k in ['diameter', 'max_spacing', 'end_offset'])
            k: values[k],
        },
        'marks_enabled': marks,
        'mark_offset': values['mark_offset'],
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

  Future<void> apply() async {
    setState(() {
      busy = true;
      applying = true;
    });
    final ok = await m.run('design.pan_apply', {
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

  String dimensions(dynamic values) =>
      (values as List).map((v) => (v as num).toStringAsFixed(4)).join(' × ');
  String metric(dynamic value) => (value as num).toStringAsFixed(4);
  @override
  Widget build(BuildContext context) {
    final active = !busy && !stale;
    const labels = {
      'thickness': 'Material thickness',
      'inside_lip': 'Finished inside lip',
      'inside_radius': 'Inside bend radius',
      'k_factor': 'K-factor (0–0.5)',
      'cutter_diameter': 'Relief cutter diameter',
      'corner_gap': 'Corner gap',
      'diameter': 'Hole diameter',
      'max_spacing': 'Maximum hole spacing',
      'end_offset': 'Hole end offset',
      'mark_offset': 'Bend-mark offset',
    };
    return PopScope(
      canPop: !busy,
      child: AlertDialog(
        title: const Text('DR Pans'),
        content: SizedBox(
          width: 820,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'All distances in ${m.units}. Selected face rectangles are finished OUTSIDE face dimensions. Lip is finished INSIDE depth. Bend compensation is an uncalibrated estimate; verify a physical sample.',
                ),
                DropdownButtonFormField<String>(
                  initialValue: mode,
                  decoration: const InputDecoration(labelText: 'Operation'),
                  items: const [
                    DropdownMenuItem(
                      value: 'create',
                      child: Text('Create pans from selected faces'),
                    ),
                    DropdownMenuItem(
                      value: 'marks',
                      child: Text('Create / update marks on selected pans'),
                    ),
                  ],
                  onChanged: active
                      ? (v) => edit(() {
                          mode = v!;
                          if (mode == 'marks') marks = true;
                        })
                      : null,
                ),
                if (mode == 'create') ...[
                  Wrap(
                    spacing: 12,
                    runSpacing: 8,
                    children: [
                      for (final id in [
                        'thickness',
                        'inside_lip',
                        'inside_radius',
                        'k_factor',
                        'cutter_diameter',
                        'corner_gap',
                      ])
                        SizedBox(
                          width: 220,
                          child: TextField(
                            controller: fields[id],
                            enabled: active,
                            decoration: InputDecoration(labelText: labels[id]),
                            onChanged: (_) => edit(() {}),
                          ),
                        ),
                    ],
                  ),
                  CheckboxListTile(
                    contentPadding: EdgeInsets.zero,
                    title: const Text('Include mounting holes'),
                    value: flags['enabled'],
                    onChanged: active
                        ? (v) => edit(() => flags['enabled'] = v == true)
                        : null,
                  ),
                  if (flags['enabled'] == true) ...[
                    Wrap(
                      spacing: 12,
                      children: [
                        for (final id in [
                          'diameter',
                          'max_spacing',
                          'end_offset',
                        ])
                          SizedBox(
                            width: 220,
                            child: TextField(
                              controller: fields[id],
                              enabled: active,
                              decoration: InputDecoration(
                                labelText: labels[id],
                              ),
                              onChanged: (_) => edit(() {}),
                            ),
                          ),
                      ],
                    ),
                    Wrap(
                      spacing: 10,
                      children: [
                        for (final side in ['top', 'bottom', 'left', 'right'])
                          FilterChip(
                            label: Text('Holes: $side'),
                            selected: flags[side]!,
                            onSelected: active
                                ? (v) => edit(() => flags[side] = v)
                                : null,
                          ),
                      ],
                    ),
                  ],
                ],
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Include engravable bend marks'),
                  value: marks,
                  onChanged: active
                      ? (v) => edit(() => marks = v == true)
                      : null,
                ),
                if (marks)
                  SizedBox(
                    width: 260,
                    child: TextField(
                      controller: fields['mark_offset'],
                      enabled: active,
                      decoration: const InputDecoration(
                        labelText: 'Bend-mark offset',
                        helperText: 'Positive toward lips; negative inward',
                      ),
                      onChanged: (_) => edit(() {}),
                    ),
                  ),
                const Text(
                  'Purple bend-zone guides are reference-only. Marks are separate machinable lines. Existing-pan mode keeps outlines, guides and holes unchanged.',
                ),
                if (busy) const LinearProgressIndicator(),
                if (error != null)
                  Text(
                    error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                if (preview != null) ...[
                  for (final row in (preview!['rows'] as List).cast<Json>())
                    Text(
                      row['face'] == null
                          ? 'Pan ${row['id']}: ${row['created']} marks created, ${row['updated']} updated.'
                          : 'Face ${dimensions(row['face'])} → blank ${dimensions(row['blank'])} ${m.units}\nBend allowance ${metric(row['allowance'])}, deduction ${metric(row['deduction'])}; ${row['holes']} holes, ${row['marks']} marks.',
                    ),
                  SizedBox(
                    height: 240,
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
                      'I reviewed the blank, holes, guides and uncalibrated bend settings.',
                    ),
                  ),
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
            onPressed: active ? calculate : null,
            child: const Text('Preview pan'),
          ),
          FilledButton(
            onPressed: active && preview != null && reviewed ? apply : null,
            child: const Text('Apply pan'),
          ),
        ],
      ),
    );
  }
}
