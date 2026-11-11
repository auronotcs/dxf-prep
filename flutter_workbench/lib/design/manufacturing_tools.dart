import 'dart:convert';
import 'package:flutter/material.dart';
import 'controller.dart';
import 'catalog_entry_editor.dart';
import 'catalog_matches_dialog.dart';
import 'catalog_action_dialog.dart';

/// Reusable catalog is isolated; turret changes are private until motion review.
class ManufacturingToolsDialog extends StatefulWidget {
  const ManufacturingToolsDialog({
    super.key,
    required this.model,
    this.library = false,
  });
  final DesignController model;
  final bool library;
  @override
  State<ManufacturingToolsDialog> createState() =>
      _ManufacturingToolsDialogState();
}

class _ManufacturingToolsDialogState extends State<ManufacturingToolsDialog> {
  DesignController get m => widget.model;
  late List<Json> tools;
  List<Json> catalog = [];
  String version = '', query = '';
  String? selected, error;
  int station = 1, recipe = 0;
  bool busy = false,
      library = false,
      editCustom = false,
      matchRecipes = false,
      manageRecipes = false,
      editScope = false;
  String? libraryNotice;
  dynamic openedRevision;
  final fields = <String, TextEditingController>{};
  final minimum = TextEditingController(), maximum = TextEditingController();
  String kind = 'end_mill', spindle = 'required';
  Json? get entry => catalog.where((v) => v['id'] == selected).firstOrNull;
  @override
  void initState() {
    super.initState();
    library = widget.library;
    openedRevision = m.state["revision"];
    tools =
        (jsonDecode(jsonEncode(m.state['manufacturing']?['tools'] ?? []))
                as List)
            .cast<Json>();
    minimum.text = '${m.state['setup']?['machine_min_rpm'] ?? 8000}';
    maximum.text = '${m.state['setup']?['machine_max_rpm'] ?? 24000}';
    for (final key in [
      'name',
      'diameter',
      'cutting_edge_length',
      'shank_diameter',
      'overall_length',
      'holder_diameter',
      'stickout',
      'spindle_rpm',
      'min_rpm',
      'max_rpm',
      'feed_rate',
      'plunge_rate',
      'max_stepdown',
      'spring_travel',
      'tip_angle',
      'thread_tpi',
      'thread_form',
    ]) {
      fields[key] = TextEditingController();
    }
    loadStation();
    fetch('catalog_list');
  }

  @override
  void dispose() {
    for (final c in fields.values) {
      c.dispose();
    }
    minimum.dispose();
    maximum.dispose();
    super.dispose();
  }

  void loadStation() {
    final t =
        tools.where((t) => t['station'] == station).firstOrNull ??
        <String, dynamic>{};
    kind = t['kind'] ?? 'end_mill';
    spindle = t['spindle_mode'] ?? (station == 8 ? 'disabled' : 'required');
    for (final key in fields.keys) {
      final value =
          {
            'spring_travel',
            'tip_angle',
            'thread_tpi',
            'thread_form',
          }.contains(key)
          ? ((t['metadata'] as Map?) ?? {})[key]
          : t[key];
      fields[key]!.text = value == null
          ? ''
          : '${['feed_rate', 'plunge_rate'].contains(key) ? (value as num) * 60 : value}';
    }
  }

  Future<void> fetch(String action, [Json params = const {}]) async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final r = await m.request('manufacturing.$action', params);
      if (r['ok'] != true) {
        throw StateError('${r['error']}');
      }
      if (!mounted) {
        return;
      }
      catalog = (r['catalog'] as List).cast<Json>();
      version = r['version'];
      editCustom = r['editCustom'] == true;
      editScope = r['editScope'] == true;
      matchRecipes = r['matchRecipes'] == true;
      manageRecipes = r['manageRecipes'] == true;
      selected = r['selectedId'] ?? selected;
      selected = catalog.any((t) => t['id'] == selected)
          ? selected
          : catalog.firstOrNull?['id'];
      final count = (entry?['recipes'] as List? ?? []).length;
      recipe = count == 0
          ? 0
          : ((r['selectedRecipe'] ?? recipe) as int).clamp(0, count - 1);
    } catch (e) {
      error = '$e';
    }
    if (mounted) {
      setState(() => busy = false);
    }
  }

  Future<void> editEntry({bool creating = false}) async {
    final draft = jsonDecode(jsonEncode(entry!)) as Json;
    final list = draft['recipes'] as List;
    if (creating) {
      list.add(<String, dynamic>{'material': '', 'qualification': 'prototype'});
    }
    final reply = await showDialog<Json>(
      context: context,
      barrierDismissible: false,
      builder: (_) => CatalogEntryEditor(
        model: m,
        entry: draft,
        version: version,
        recipeIndex: creating ? list.length - 1 : recipe,
        creating: creating,
        editScope: editScope,
      ),
    );
    if (!mounted || reply == null) return;
    selected = reply['selectedId'] ?? selected;
    recipe = reply['selectedRecipe'] ?? recipe;
    libraryNotice = (reply['downgradedRecipes'] as List? ?? []).isEmpty
        ? 'Library saved. Job tools are unchanged.'
        : 'Library saved. Changed cutting inputs are Prototype and need a new test record. Job tools are unchanged.';
    await fetch('catalog_list');
  }

  Future<void> libraryAction(String action) async {
    final reply = await showDialog<Json>(
      context: context,
      barrierDismissible: false,
      builder: (_) => CatalogActionDialog(
        model: m,
        entry: entry!,
        version: version,
        recipeIndex: recipe,
        action: action,
      ),
    );
    if (!mounted || reply == null) return;
    selected = reply['selectedId'] ?? selected;
    recipe = reply['selectedRecipe'] ?? 0;
    libraryNotice =
        'Library updated. Existing drawings and turret drafts are unchanged.';
    await fetch('catalog_list');
  }

  Future<void> matchJobRecipes() async {
    final reply = await showDialog<Json>(
      context: context,
      barrierDismissible: false,
      builder: (_) => CatalogMatchesDialog(model: m, station: station),
    );
    if (!mounted || reply == null) return;
    setState(() {
      tools.removeWhere((v) => v['station'] == station);
      tools.add(reply['tool'] as Json);
      library = false;
      libraryNotice = '${reply['note']}';
      loadStation();
    });
  }

  void saveStation() {
    final old =
        tools.where((t) => t['station'] == station).firstOrNull ??
        <String, dynamic>{};
    final t = <String, dynamic>{
      ...old,
      'station': station,
      'kind': kind,
      'spindle_mode': spindle,
      'metadata': Map<String, dynamic>.from((old['metadata'] as Map?) ?? {}),
    };
    for (final key in fields.keys) {
      final text = fields[key]!.text.trim();
      if ({
        'spring_travel',
        'tip_angle',
        'thread_tpi',
        'thread_form',
      }.contains(key)) {
        final meta = t['metadata'] as Json;
        if (text.isEmpty) {
          meta.remove(key);
        } else {
          meta[key] = {'thread_tpi', 'thread_form'}.contains(key)
              ? text
              : double.parse(text);
        }
        continue;
      }
      // Preserve exact stored values when the displayed field was not edited.
      // A metric feed's multiply/divide roundtrip can otherwise erase its recipe.
      if (key != 'name' &&
          old[key] != null &&
          text ==
              '${['feed_rate', 'plunge_rate'].contains(key) ? (old[key] as num) * 60 : old[key]}') {
        t[key] = old[key];
        continue;
      }
      t[key] = key == 'name'
          ? text
          : text.isEmpty
          ? null
          : ['spindle_rpm', 'min_rpm', 'max_rpm'].contains(key)
          ? int.parse(text)
          : double.parse(text) /
                (['feed_rate', 'plunge_rate'].contains(key) ? 60 : 1);
    }
    if (t['name'].isEmpty || t['diameter'] == null) {
      throw StateError('Enter a tool name and diameter.');
    }
    if (spindle == 'disabled') {
      t['spindle_rpm'] = null;
    }
    if (old.isNotEmpty &&
        [
          'diameter',
          'kind',
          'feed_rate',
          'plunge_rate',
          'spindle_rpm',
          'max_stepdown',
        ].any((k) => t[k] != old[k])) {
      t['metadata'] = <String, dynamic>{...((t['metadata'] as Json?) ?? {})}
        ..remove('loaded_recipe')
        ..remove('material_recipe');
    }
    tools.removeWhere((v) => v['station'] == station);
    tools.add(t);
  }

  Future<void> assign() async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final r = await m.request('manufacturing.catalog_resolve', {
        'id': selected,
        'recipe': recipe,
        'station': station,
        'version': version,
      });
      if (r['ok'] != true) {
        throw StateError('${r['error']}');
      }
      if (!mounted) {
        return;
      }
      tools.removeWhere((v) => v['station'] == station);
      tools.add(r['tool'] as Json);
      library = false;
      loadStation();
    } catch (e) {
      error = '$e';
    }
    if (mounted) {
      setState(() => busy = false);
    }
  }

  Future<void> loadRack() async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final r = await m.request('manufacturing.catalog_rack_resolve', {
        'version': version,
      });
      if (r['ok'] != true) {
        throw StateError('${r['error']}');
      }
      if (!mounted) {
        return;
      }
      final rack = r['rack'] as Json;
      tools = (rack['tools'] as List).cast<Json>();
      minimum.text = '${rack['minimum_rpm']}';
      maximum.text = '${rack['maximum_rpm']}';
      loadStation();
      library = false;
    } catch (e) {
      error = '$e';
    }
    if (mounted) {
      setState(() => busy = false);
    }
  }

  Widget field(String key, String label) => SizedBox(
    width: 185,
    child: TextField(
      controller: fields[key],
      decoration: InputDecoration(
        labelText: label,
        isDense: true,
        border: const OutlineInputBorder(),
      ),
    ),
  );
  @override
  Widget build(BuildContext context) {
    final recipes = (entry?['recipes'] as List? ?? []).cast<Json>();
    final units = m.state['units'] == 'mm' ? 'mm' : 'in';
    final factor = units == 'mm' ? 25.4 : 1.0;
    String display(dynamic v) =>
        v is num ? (v * factor).toStringAsPrecision(6) : '—';
    return Dialog(
      child: SizedBox(
        width: 1080,
        height: 690,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            children: [
              Row(
                children: [
                  Text(
                    library ? 'Tool & Recipe Library' : 'Turret Setup',
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  const Spacer(),
                  TextButton(
                    onPressed: busy
                        ? null
                        : () => setState(() => library = !library),
                    child: Text(library ? 'Turret Setup' : 'Tool Library'),
                  ),
                  IconButton(
                    tooltip: 'Close tools',
                    onPressed: busy ? null : () => Navigator.pop(context),
                    icon: const Icon(Icons.close),
                  ),
                ],
              ),
              const Text(
                'Library entries are reusable. Loaded stations belong to this job; changes require toolpath review.',
              ),
              const SizedBox(height: 8),
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  onPressed: busy || !matchRecipes ? null : matchJobRecipes,
                  icon: const Icon(Icons.manage_search),
                  label: const Text('Match job recipes'),
                ),
              ),
              if (libraryNotice != null && !library) Text(libraryNotice!),
              SizedBox(
                height: 42,
                child: ListView(
                  scrollDirection: Axis.horizontal,
                  children: [
                    for (var n = 1; n <= 12; n++)
                      Padding(
                        padding: const EdgeInsets.only(right: 4),
                        child: ChoiceChip(
                          label: Text(
                            'T$n${tools.any((v) => v['station'] == n) ? ' •' : ''}',
                          ),
                          selected: station == n,
                          onSelected: busy
                              ? null
                              : (_) => setState(() {
                                  if (fields['name']!.text.trim().isNotEmpty) {
                                    try {
                                      saveStation();
                                    } catch (e) {
                                      error = '$e';
                                      return;
                                    }
                                  }
                                  station = n;
                                  loadStation();
                                  error = null;
                                }),
                        ),
                      ),
                  ],
                ),
              ),
              Expanded(
                child: library
                    ? SingleChildScrollView(
                        child: Column(
                          children: [
                            TextField(
                              decoration: const InputDecoration(
                                prefixIcon: Icon(Icons.search),
                                hintText:
                                    'Search tool name, manufacturer or type',
                              ),
                              onChanged: (s) =>
                                  setState(() => query = s.toLowerCase()),
                            ),
                            SizedBox(
                              height: (catalog.length * 64)
                                  .clamp(72, 180)
                                  .toDouble(),
                              child: ListView(
                                children: [
                                  for (final t in catalog.where(
                                    (v) =>
                                        '${v['name']} ${v['manufacturer']} ${v['kind']}'
                                            .toLowerCase()
                                            .contains(query),
                                  ))
                                    ListTile(
                                      dense: true,
                                      selected: t['id'] == selected,
                                      title: Text('${t['name']}'),
                                      subtitle: Text(
                                        '${t['kind']} · Ø ${display(t['diameter_in'])} $units',
                                      ),
                                      onTap: () => setState(() {
                                        selected = t['id'];
                                        recipe = 0;
                                      }),
                                    ),
                                ],
                              ),
                            ),
                            if (entry != null) ...[
                              if (libraryNotice != null) Text(libraryNotice!),
                              Text(
                                '${entry!['notes'] ?? ''}',
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                              ),
                              if (recipes.isNotEmpty)
                                DropdownButtonFormField<int>(
                                  key: ValueKey('$selected-$version-$recipe'),
                                  initialValue: recipe,
                                  decoration: const InputDecoration(
                                    labelText: 'Material / cutting recipe',
                                  ),
                                  isExpanded: true,
                                  items: [
                                    for (var i = 0; i < recipes.length; i++)
                                      DropdownMenuItem(
                                        value: i,
                                        child: Text(
                                          '${recipes[i]['material']}',
                                          overflow: TextOverflow.ellipsis,
                                        ),
                                      ),
                                  ],
                                  onChanged: (v) =>
                                      setState(() => recipe = v ?? 0),
                                ),
                              if (recipes.isNotEmpty)
                                Text(
                                  'RPM ${recipes[recipe]['rpm'] ?? 'Off'} · feed ${display(recipes[recipe]['feed'])} $units/min · plunge ${display(recipes[recipe]['plunge'])} $units/min · step ${display(recipes[recipe]['stepdown'])} $units',
                                ),
                              if (recipes.isNotEmpty)
                                Text(
                                  'Qualification: ${recipes[recipe]['qualification'] ?? 'prototype'}',
                                ),
                              Wrap(
                                spacing: 8,
                                children: [
                                  FilledButton(
                                    onPressed:
                                        busy ||
                                            recipes.isEmpty ||
                                            recipes[recipe]['qualification'] ==
                                                'retired'
                                        ? null
                                        : assign,
                                    child: Text('Load into T$station draft'),
                                  ),
                                  TextButton(
                                    onPressed: busy
                                        ? null
                                        : () => fetch('catalog_save', {
                                            'station': station,
                                            'version': version,
                                          }),
                                    child: const Text(
                                      'Save applied station to library',
                                    ),
                                  ),
                                  if (entry!['custom'] == true)
                                    TextButton(
                                      onPressed:
                                          busy ||
                                              !editCustom ||
                                              recipes.isEmpty ||
                                              recipes[recipe]['qualification'] ==
                                                  'retired'
                                          ? null
                                          : editEntry,
                                      child: const Text(
                                        'Edit custom tool / recipe',
                                      ),
                                    ),
                                  if (entry!['custom'] == true && !editCustom)
                                    const Text(
                                      'Save and reopen this drawing to enable library editing.',
                                    ),
                                  if (entry!['custom'] == true &&
                                      manageRecipes) ...[
                                    TextButton(
                                      onPressed: busy
                                          ? null
                                          : () => editEntry(creating: true),
                                      child: const Text('Add recipe'),
                                    ),
                                    TextButton(
                                      onPressed: busy || recipes.isEmpty
                                          ? null
                                          : () => libraryAction(
                                              'catalog_recipe_duplicate',
                                            ),
                                      child: const Text('Duplicate recipe'),
                                    ),
                                    TextButton(
                                      onPressed: busy || recipes.length <= 1
                                          ? null
                                          : () => libraryAction(
                                              'catalog_recipe_remove',
                                            ),
                                      child: const Text('Remove recipe'),
                                    ),
                                    TextButton(
                                      onPressed: busy || recipes.isEmpty
                                          ? null
                                          : () => libraryAction(
                                              'catalog_recipe_status',
                                            ),
                                      child: Text(
                                        recipes.isNotEmpty &&
                                                recipes[recipe]['qualification'] ==
                                                    'retired'
                                            ? 'Reactivate recipe'
                                            : 'Retire recipe',
                                      ),
                                    ),
                                    TextButton(
                                      onPressed: busy
                                          ? null
                                          : () => libraryAction(
                                              'catalog_tool_duplicate',
                                            ),
                                      child: const Text(
                                        'Duplicate custom tool',
                                      ),
                                    ),
                                  ],
                                  if (entry!['custom'] == true)
                                    TextButton(
                                      onPressed: busy
                                          ? null
                                          : () => fetch('catalog_delete', {
                                              'id': selected,
                                              'version': version,
                                            }),
                                      child: const Text('Delete custom entry'),
                                    ),
                                ],
                              ),
                            ],
                          ],
                        ),
                      )
                    : ListView(
                        children: [
                          Padding(
                            padding: const EdgeInsets.symmetric(vertical: 8),
                            child: Text(
                              'Station T$station${station == 6
                                  ? ' · maximum 12,000 RPM'
                                  : station == 8
                                  ? ' · spindle disabled'
                                  : ''}',
                              style: Theme.of(context).textTheme.titleMedium,
                            ),
                          ),
                          Wrap(
                            spacing: 12,
                            runSpacing: 16,
                            children: [
                              field('name', 'Tool name'),
                              SizedBox(
                                width: 185,
                                child: DropdownButtonFormField<String>(
                                  isExpanded: true,
                                  key: ValueKey('kind$station$kind'),
                                  initialValue: kind,
                                  decoration: const InputDecoration(
                                    labelText: 'Tool type',
                                  ),
                                  items: [
                                    for (final k in [
                                      'end_mill',
                                      'ball_nose',
                                      'bull_nose',
                                      'drill',
                                      'v_bit',
                                      'planing',
                                      'engraver',
                                      'drag_knife',
                                      'brush',
                                      'marker',
                                      'thread_mill',
                                      'other',
                                    ])
                                      DropdownMenuItem(
                                        value: k,
                                        child: Text(k.replaceAll('_', ' ')),
                                      ),
                                  ],
                                  onChanged: (v) => setState(() => kind = v!),
                                ),
                              ),
                              field('diameter', 'Diameter (${m.units})'),
                              field(
                                'cutting_edge_length',
                                'Cutting length (${m.units})',
                              ),
                              field('shank_diameter', 'Shank (${m.units})'),
                              field(
                                'overall_length',
                                'Overall length (${m.units})',
                              ),
                              field('holder_diameter', 'Holder (${m.units})'),
                              field('stickout', 'Stickout (${m.units})'),
                              if (kind == 'engraver') ...[
                                field(
                                  'spring_travel',
                                  'Spring travel (${m.units})',
                                ),
                                field('tip_angle', 'Tip angle (degrees)'),
                              ],
                              if (kind == 'thread_mill') ...[
                                field(
                                  'thread_tpi',
                                  'Qualified TPI (comma separated)',
                                ),
                                field(
                                  'thread_form',
                                  'Thread form (e.g. UN 60)',
                                ),
                              ],
                              SizedBox(
                                width: 185,
                                child: DropdownButtonFormField<String>(
                                  isExpanded: true,
                                  key: ValueKey('spindle$station$spindle'),
                                  initialValue: spindle,
                                  decoration: const InputDecoration(
                                    labelText: 'Spindle mode',
                                  ),
                                  items: [
                                    for (final v in [
                                      'required',
                                      'optional',
                                      'disabled',
                                    ])
                                      DropdownMenuItem(
                                        value: v,
                                        child: Text(v),
                                      ),
                                  ],
                                  onChanged: (v) =>
                                      setState(() => spindle = v!),
                                ),
                              ),
                              field('spindle_rpm', 'RPM'),
                              field('min_rpm', 'Tool minimum RPM'),
                              field('max_rpm', 'Tool maximum RPM'),
                              field('feed_rate', 'Feed (${m.units}/min)'),
                              field('plunge_rate', 'Plunge (${m.units}/min)'),
                              field(
                                'max_stepdown',
                                'Max stepdown (${m.units})',
                              ),
                            ],
                          ),
                          const SizedBox(height: 14),
                          Wrap(
                            spacing: 8,
                            children: [
                              FilledButton.tonal(
                                onPressed: busy
                                    ? null
                                    : () {
                                        try {
                                          saveStation();
                                          setState(() => error = null);
                                        } catch (e) {
                                          setState(() => error = '$e');
                                        }
                                      },
                                child: const Text('Keep station in draft'),
                              ),
                              TextButton(
                                onPressed: busy
                                    ? null
                                    : () => setState(() {
                                        tools.removeWhere(
                                          (t) => t['station'] == station,
                                        );
                                        loadStation();
                                      }),
                                child: const Text('Clear station in draft'),
                              ),
                            ],
                          ),
                          const Divider(),
                          Wrap(
                            spacing: 12,
                            children: [
                              SizedBox(
                                width: 185,
                                child: TextField(
                                  controller: minimum,
                                  decoration: const InputDecoration(
                                    labelText: 'Machine minimum RPM',
                                  ),
                                ),
                              ),
                              SizedBox(
                                width: 185,
                                child: TextField(
                                  controller: maximum,
                                  decoration: const InputDecoration(
                                    labelText: 'Machine maximum RPM',
                                  ),
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 12),
                          Wrap(
                            spacing: 8,
                            children: [
                              TextButton(
                                onPressed: busy ? null : loadRack,
                                child: const Text('Load saved turret preset'),
                              ),
                              TextButton(
                                onPressed: busy
                                    ? null
                                    : () => fetch('catalog_rack_save', {
                                        'version': version,
                                      }),
                                child: const Text(
                                  'Save applied turret as preset',
                                ),
                              ),
                            ],
                          ),
                          for (final t in tools)
                            Text(
                              'T${t['station']} · ${t['name']} · Ø ${t['diameter']} ${m.units}',
                            ),
                        ],
                      ),
              ),
              if (busy) const LinearProgressIndicator(),
              if (error != null)
                Text(
                  error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  TextButton(
                    onPressed: busy ? null : () => Navigator.pop(context),
                    child: const Text('Cancel'),
                  ),
                  FilledButton(
                    onPressed: busy
                        ? null
                        : () {
                            try {
                              if (!library &&
                                  fields['name']!.text.trim().isNotEmpty) {
                                saveStation();
                              }
                              if (m.state['revision'] != openedRevision) {
                                throw StateError(
                                  'Job changed. Reopen Turret Setup before reviewing.',
                                );
                              }
                              Navigator.pop(context, <String, dynamic>{
                                'tools': tools,
                                'minimum_rpm': int.parse(minimum.text),
                                'maximum_rpm': int.parse(maximum.text),
                              });
                            } catch (e) {
                              setState(() => error = '$e');
                            }
                          },
                    child: const Text('Review turret changes'),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
