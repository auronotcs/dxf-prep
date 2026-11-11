import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'controller.dart';

class PaletteDialog extends StatefulWidget {
  const PaletteDialog({super.key, required this.model});
  final DesignController model;
  @override
  State<PaletteDialog> createState() => _PaletteDialogState();
}

class _PaletteDialogState extends State<PaletteDialog> {
  List<Json> palettes = [];
  String filter = '', query = '', digest = '';
  Json? selectedPalette, selectedSwatch;
  bool working = true;
  String? error;
  @override
  void initState() {
    super.initState();
    load();
  }

  Future<Json> request(String command, [Json params = const {}]) async {
    final result = await widget.model.request(command, params);
    if (result['ok'] != true) throw StateError(result['error'].toString());
    return result;
  }

  Future<void> load() async {
    try {
      final result = await request('palette.list');
      if (!mounted) return;
      setState(() {
        palettes = (result['palettes'] as List).cast<Json>();
        digest = result['digest'] as String;
        if (!palettes.any((p) => p['id'] == filter)) filter = '';
        selectedPalette = selectedSwatch = null;
        error = null;
      });
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  Future<bool> confirm(String title, String body, String action) async =>
      await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(title),
          content: Text(body),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: Text(action),
            ),
          ],
        ),
      ) ??
      false;

  Future<void> importPalette() async {
    setState(() {
      working = true;
      error = null;
    });
    try {
      final path = await const MethodChannel(
        'workbench/design',
      ).invokeMethod<String>('palette');
      if (path == null || !mounted) return;
      final proposal = await request('palette.import_preview', {'path': path});
      if (!mounted) return;
      final accepted = await confirm(
        'Import palette',
        '${proposal['name']} · ${proposal['manufacturer']}\n${proposal['count']} swatches.\n\n'
            '${proposal['replaces'] == null ? 'Add to the Flutter palette library?' : 'Replace ${proposal['replaces']} in the Flutter palette library?'}'
            '\nExisting artwork keeps its stored colors.',
        proposal['replaces'] == null ? 'Import' : 'Replace',
      );
      if (!accepted || !mounted) return;
      await request('palette.import_apply', {'token': proposal['token']});
      await load();
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  Future<void> removePalette() async {
    final palette = selectedPalette!;
    if (!await confirm(
          'Remove palette',
          'Remove ${palette['name']} from the Flutter library? Existing artwork keeps its stored colors.',
          'Remove',
        ) ||
        !mounted) {
      return;
    }
    setState(() => working = true);
    try {
      await request('palette.remove', {
        'palette_id': palette['id'],
        'digest': digest,
      });
      await load();
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  Future<void> choose() async {
    setState(() => working = true);
    try {
      final result = await request('palette.choose', {
        'palette_id': selectedPalette!['id'],
        'swatch_id': selectedSwatch!['id'],
      });
      if (mounted) Navigator.pop(context, result);
    } catch (e) {
      if (mounted) {
        setState(() {
          error = e.toString();
          working = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final rows = <(Json, Json)>[];
    for (final p in palettes) {
      if (filter.isNotEmpty && filter != p['id']) continue;
      for (final s in (p['swatches'] as List).cast<Json>()) {
        final searchable =
            '${s['name']} ${s['product_code']} ${p['name']} ${p['manufacturer']} ${p['collection']} ${p['source']} ${p['version']}'
                .toLowerCase();
        if (searchable.contains(query.trim().toLowerCase())) rows.add((p, s));
      }
    }
    return PopScope(
      canPop: !working,
      child: AlertDialog(
        title: const Text('Color palettes'),
        content: SizedBox(
          width: 720,
          height: math.max(
            160,
            math.min(500, MediaQuery.sizeOf(context).height - 200),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      enabled: !working,
                      decoration: const InputDecoration(
                        labelText: 'Search name, code or manufacturer',
                        prefixIcon: Icon(Icons.search),
                      ),
                      onChanged: (v) => setState(() {
                        query = v;
                        selectedPalette = selectedSwatch = null;
                      }),
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton(
                    tooltip: 'Refresh palettes',
                    onPressed: working
                        ? null
                        : () {
                            setState(() => working = true);
                            load();
                          },
                    icon: const Icon(Icons.refresh),
                  ),
                  TextButton.icon(
                    onPressed: working ? null : importPalette,
                    icon: const Icon(Icons.file_open_outlined),
                    label: const Text('Import…'),
                  ),
                ],
              ),
              DropdownButton<String>(
                value: filter,
                isExpanded: true,
                items: [
                  const DropdownMenuItem(
                    value: '',
                    child: Text('All palettes'),
                  ),
                  for (final p in palettes)
                    DropdownMenuItem(
                      value: p['id'] as String,
                      child: Text(
                        p['name'] as String,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                ],
                onChanged: working
                    ? null
                    : (v) => setState(() {
                        filter = v!;
                        selectedPalette = selectedSwatch = null;
                      }),
              ),
              if (working) const LinearProgressIndicator(),
              Expanded(
                child: rows.isEmpty
                    ? Center(
                        child: Text(
                          palettes.isEmpty
                              ? 'No palettes imported yet. Import a CAM Workbench JSON palette or a supported Sherwin-Williams XLSX/ZIP file.'
                              : 'No matching swatches.',
                        ),
                      )
                    : ListView.builder(
                        itemCount: rows.length,
                        itemBuilder: (context, i) {
                          final (p, s) = rows[i];
                          final rgb = s['color']['preview_rgb'] as List;
                          return ListTile(
                            dense: true,
                            selected: identical(s, selectedSwatch),
                            leading: Container(
                              width: 32,
                              height: 24,
                              decoration: BoxDecoration(
                                color: Color.fromARGB(
                                  255,
                                  (rgb[0] as num).toInt(),
                                  (rgb[1] as num).toInt(),
                                  (rgb[2] as num).toInt(),
                                ),
                                border: Border.all(color: Colors.grey),
                              ),
                            ),
                            title: Text(
                              '${s['name']} ${s['product_code']}',
                              overflow: TextOverflow.ellipsis,
                            ),
                            subtitle: Text(
                              '${p['name']} · ${p['manufacturer']} · ${p['collection']}',
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                            onTap: working
                                ? null
                                : () => setState(() {
                                    selectedPalette = p;
                                    selectedSwatch = s;
                                  }),
                          );
                        },
                      ),
              ),
              if (selectedSwatch != null)
                SizedBox(
                  height: 68,
                  child: SingleChildScrollView(
                    child: SelectableText(
                      '${selectedSwatch!['color']['model'].toString().toUpperCase()} · ${(selectedSwatch!['color']['channels'] as List).join(', ')}\n'
                      'Profile: ${selectedSwatch!['color']['profile']} · Version: ${selectedPalette!['version']}\nSource: ${selectedPalette!['source']}',
                    ),
                  ),
                ),
              Text(
                '${rows.length} swatches · Screen colors are approximate.',
                style: const TextStyle(fontSize: 11),
              ),
              if (error != null)
                Text(
                  error!,
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: working || selectedPalette == null
                ? null
                : removePalette,
            child: const Text('Remove palette'),
          ),
          TextButton(
            onPressed: working ? null : () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: working || selectedSwatch == null ? null : choose,
            child: const Text('Use swatch'),
          ),
        ],
      ),
    );
  }
}
