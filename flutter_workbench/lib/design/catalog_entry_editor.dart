import 'package:flutter/material.dart';
import 'controller.dart';

/// Edits reusable data only. Loading a cutter into a job remains a separate draft.
class CatalogEntryEditor extends StatefulWidget {
  const CatalogEntryEditor({
    super.key,
    required this.model,
    required this.entry,
    required this.version,
    required this.recipeIndex,
    this.editScope = false,
    this.creating = false,
  });
  final DesignController model;
  final Json entry;
  final String version;
  final int recipeIndex;
  final bool editScope;
  final bool creating;
  @override
  State<CatalogEntryEditor> createState() => _CatalogEntryEditorState();
}

class _CatalogEntryEditorState extends State<CatalogEntryEditor> {
  static const roleNames = {
    'any': 'Any role',
    'mark_engrave': 'Mark / engrave',
    'drill_thread': 'Drill / thread',
    'shelf_recess': 'Shelf / recess',
    'pocket_fill': 'Pocket / cleanout',
    'inside_profile': 'Inside profile',
    'outside_profile': 'Outside profile',
    'finish_cutoff': 'Finish / cutoff',
  };
  static const entryNames = {
    'plunge': 'Plunge',
    'ramp': 'Ramp',
    'helical': 'Helical',
    'pre_drilled': 'Pre-drilled',
    'edge': 'Edge',
  };
  late Set<String> roles, entries;
  static const geometry = [
    'diameter',
    'cutting_edge_length',
    'shank_diameter',
    'overall_length',
    'holder_diameter',
    'stickout',
  ];
  final fields = <String, TextEditingController>{};
  late final String units;
  late final double factor;
  late String spindle, qualification;
  String? error;
  bool busy = false;
  @override
  void initState() {
    super.initState();
    units = widget.model.state['units'] == 'mm' ? 'mm' : 'in';
    factor = units == 'mm' ? 25.4 : 1;
    final t = widget.entry;
    final r = (t['recipes'] as List)[widget.recipeIndex] as Json;
    final rawRoles = r['process_roles'] ?? ['any'];
    roles =
        (rawRoles is String
                ? rawRoles.split(',').map((s) => s.trim())
                : (rawRoles as List).cast<String>())
            .toSet();
    entries = ((r['entry_strategies'] as List?) ?? []).cast<String>().toSet();
    spindle = t['spindle_mode'] ?? 'required';
    qualification = r['qualification'] == 'shop_tested'
        ? 'shop_tested'
        : 'prototype';
    void put(String key, dynamic value, [double scale = 1]) {
      fields[key] = TextEditingController(
        text: value == null
            ? ''
            : value is num
            ? '${value * scale}'
            : '$value',
      );
    }

    put('name', t['name']);
    for (final k in geometry) {
      put(k, t['${k}_in'], factor);
    }
    for (final k in ['min_rpm', 'max_rpm']) {
      put(k, t[k]);
    }
    put('material', r['material']);
    put('material_family', r['material_family']);
    put('thickness_min', r['thickness_min_in'], factor);
    put('thickness_max', r['thickness_max_in'], factor);
    put('rpm', r['rpm']);
    for (final k in ['feed', 'plunge', 'stepdown', 'stepover']) {
      put(k, r[k], factor);
    }
    put('evidence', r['qualification_evidence']);
  }

  @override
  void dispose() {
    for (final c in fields.values) {
      c.dispose();
    }
    super.dispose();
  }

  num? value(String key, {bool integer = false, bool optional = false}) {
    final s = fields[key]!.text.trim();
    if (optional && s.isEmpty) return null;
    final n = double.tryParse(s);
    if (n == null ||
        !n.isFinite ||
        n <= 0 ||
        (integer && n != n.truncateToDouble())) {
      throw FormatException(
        '$key must be a positive ${integer ? 'whole ' : ''}number.',
      );
    }
    return integer ? n.toInt() : n;
  }

  Future<void> save() async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final t = <String, dynamic>{
        'name': fields['name']!.text.trim(),
        'spindle_mode': spindle,
      };
      for (final k in geometry) {
        t[k] = value(k, optional: k != 'diameter');
      }
      for (final k in ['min_rpm', 'max_rpm']) {
        t[k] = value(k, optional: true, integer: true);
      }
      final r = <String, dynamic>{
        'material': fields['material']!.text.trim(),
        'rpm': value('rpm', integer: true, optional: spindle == 'disabled'),
      };
      for (final k in ['feed', 'plunge', 'stepdown', 'stepover']) {
        r[k] = value(k, optional: k == 'stepover');
      }
      Json? scope;
      if (widget.editScope) {
        final minimum = value('thickness_min', optional: true),
            maximum = value('thickness_max', optional: true);
        if (minimum != null && maximum != null && minimum > maximum) {
          throw const FormatException(
            'Minimum thickness exceeds maximum thickness.',
          );
        }
        if (roles.isEmpty) {
          throw const FormatException('Choose at least one process role.');
        }
        scope = {
          'material_family': fields['material_family']!.text.trim(),
          'thickness_min': minimum,
          'thickness_max': maximum,
          'process_roles': roles.toList()..sort(),
          'entry_strategies': entries.toList()..sort(),
        };
      }
      final reply = await widget.model.request(
        widget.creating
            ? 'manufacturing.catalog_recipe_add'
            : 'manufacturing.catalog_update',
        {
          'id': widget.entry['id'],
          'version': widget.version,
          'units': units,
          if (!widget.creating) 'tool': t,
          if (!widget.creating) 'recipe_index': widget.recipeIndex,
          'recipe': r,
          'scope': ?scope,
          if (!widget.creating) 'qualification': qualification,
          if (!widget.creating) 'evidence': fields['evidence']!.text.trim(),
        },
      );
      if (reply['ok'] != true) throw StateError('${reply['error']}');
      if (mounted) Navigator.pop(context, reply);
    } catch (e) {
      if (mounted) {
        setState(() {
          error = '$e';
          busy = false;
        });
      }
    }
  }

  Widget field(String key, String label, {bool wide = false}) => SizedBox(
    width: wide ? 560 : 240,
    child: TextField(
      controller: fields[key],
      enabled: !busy,
      maxLines: key == 'evidence' ? 4 : 1,
      decoration: InputDecoration(
        labelText: label,
        border: const OutlineInputBorder(),
        isDense: true,
      ),
    ),
  );
  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !busy,
    child: AlertDialog(
      title: Text(
        widget.creating ? 'Add material recipe' : 'Edit custom tool & recipe',
      ),
      scrollable: true,
      content: SizedBox(
        width: 620,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Saving updates the reusable library only. Load into a turret draft separately to review job changes.',
            ),
            const SizedBox(height: 16),
            Text(
              'Cutter · ${widget.entry['kind']} · $units',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 12),
            if (!widget.creating)
              Wrap(
                spacing: 12,
                runSpacing: 14,
                children: [
                  field('name', 'Tool name'),
                  field('diameter', 'Diameter ($units)'),
                  field('cutting_edge_length', 'Cutting edge length ($units)'),
                  field('shank_diameter', 'Shank diameter ($units)'),
                  field('overall_length', 'Overall length ($units)'),
                  field('holder_diameter', 'Holder diameter ($units)'),
                  field('stickout', 'Stickout ($units)'),
                  field('min_rpm', 'Minimum RPM (optional)'),
                  field('max_rpm', 'Maximum RPM (optional)'),
                  SizedBox(
                    width: 240,
                    child: DropdownButtonFormField<String>(
                      initialValue: spindle,
                      decoration: const InputDecoration(
                        labelText: 'Spindle mode',
                      ),
                      items: [
                        for (final v in ['required', 'optional', 'disabled'])
                          DropdownMenuItem(value: v, child: Text(v)),
                      ],
                      onChanged: busy
                          ? null
                          : (v) => setState(() => spindle = v!),
                    ),
                  ),
                ],
              ),
            const SizedBox(height: 22),
            Text(
              'Material recipe ${widget.recipeIndex + 1}',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 12,
              runSpacing: 14,
              children: [
                field('material', 'Material / recipe name'),
                field('rpm', 'Recipe RPM (blank if disabled)'),
                field('feed', 'Cut feed ($units/min)'),
                field('plunge', 'Plunge feed ($units/min)'),
                field('stepdown', 'Stepdown ($units)'),
                field('stepover', 'Stepover ($units, optional)'),
              ],
            ),
            const SizedBox(height: 18),
            if (widget.editScope) ...[
              Text(
                'Recipe applicability',
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 12,
                runSpacing: 14,
                children: [
                  field('material_family', 'Material family'),
                  field(
                    'thickness_min',
                    'Minimum thickness ($units, optional)',
                  ),
                  field(
                    'thickness_max',
                    'Maximum thickness ($units, optional)',
                  ),
                ],
              ),
              const SizedBox(height: 12),
              const Text('Applicable process roles'),
              Wrap(
                spacing: 6,
                children: [
                  for (final item in roleNames.entries)
                    FilterChip(
                      label: Text(item.value),
                      selected: roles.contains(item.key),
                      onSelected: busy
                          ? null
                          : (selected) => setState(() {
                              if (selected && item.key == 'any') {
                                roles = {'any'};
                              } else if (selected) {
                                roles.remove('any');
                                roles.add(item.key);
                              } else {
                                roles.remove(item.key);
                              }
                            }),
                    ),
                ],
              ),
              if (roles.any((v) => !roleNames.containsKey(v)))
                Text(
                  'Unrecognized saved roles: ${roles.where((v) => !roleNames.containsKey(v)).join(', ')}. Choose Any role to reset.',
                ),
              const SizedBox(height: 12),
              const Text(
                'Allowed entry strategies (none selected means unrestricted)',
              ),
              Wrap(
                spacing: 6,
                children: [
                  for (final item in entryNames.entries)
                    FilterChip(
                      label: Text(item.value),
                      selected: entries.contains(item.key),
                      onSelected: busy
                          ? null
                          : (selected) => setState(() {
                              selected
                                  ? entries.add(item.key)
                                  : entries.remove(item.key);
                            }),
                    ),
                ],
              ),
              TextButton(
                onPressed: busy ? null : () => setState(() => entries.clear()),
                child: const Text('Clear entry restrictions'),
              ),
              if (entries.any((v) => !entryNames.containsKey(v)))
                Text(
                  'Unrecognized saved entries: ${entries.where((v) => !entryNames.containsKey(v)).join(', ')}. Clear restrictions to reset.',
                ),
              const Text(
                'A blank material family uses the recipe name. Blank thickness bounds are unbounded. Scope records describe applicability; they do not qualify a machine, fixture or job. Changing scope resets this recipe to Prototype.',
              ),
              const SizedBox(height: 18),
            ] else
              const Text(
                'Save and reopen this drawing to enable recipe applicability editing.',
              ),
            Text(
              widget.creating
                  ? 'New recipes start as Prototype. Save the settings, then record a physical test separately.'
                  : 'Changing cutter inputs resets active recipes to Prototype; retired recipes remain retired. Changing cutting inputs resets this recipe. Save those changes first, then record a new physical test. A saved test does not qualify the current job or setup.',
            ),
            if (!widget.creating)
              DropdownButtonFormField<String>(
                initialValue: qualification,
                decoration: const InputDecoration(
                  labelText: 'Qualification record',
                ),
                items: const [
                  DropdownMenuItem(
                    value: 'prototype',
                    child: Text('Prototype / unqualified'),
                  ),
                  DropdownMenuItem(
                    value: 'shop_tested',
                    child: Text('Shop tested — evidence required'),
                  ),
                ],
                onChanged: busy
                    ? null
                    : (v) => setState(() => qualification = v!),
              ),
            const SizedBox(height: 16),
            if (!widget.creating)
              field(
                'evidence',
                'Evidence: material/thickness, setup, date and result',
                wide: true,
              ),
            if (error != null)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(
                  error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: busy ? null : () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: busy ? null : save,
          child: Text(busy ? 'Saving…' : 'Save library entry'),
        ),
      ],
    ),
  );
}
