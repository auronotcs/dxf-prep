import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'controller.dart';

class ShopPdfDialog extends StatefulWidget {
  const ShopPdfDialog({super.key, required this.model});
  final DesignController model;
  @override
  State<ShopPdfDialog> createState() => _ShopPdfDialogState();
}

class _ShopPdfDialogState extends State<ShopPdfDialog> {
  static const platform = MethodChannel('workbench/design');
  final selected = <int>{};
  final denominator = TextEditingController(text: '1');
  String pageSize = 'Tabloid', orientation = 'landscape', scaleMode = 'fit';
  String message = 'Choose plates and settings, then prepare the PDF.';
  String? savedPath;
  bool working = false, openAfter = true;
  Json? preview;
  DesignController get m => widget.model;
  @override
  void initState() {
    super.initState();
    if (m.plates.isNotEmpty) {
      selected.add((m.state['activePlate'] ?? m.plates.first['number']) as int);
    }
    final saved = m.preferences['shopDrawing'];
    final prefs = saved is Json ? saved : <String, dynamic>{};
    if ([
      'Letter',
      'Tabloid',
      'ARCH C',
      'ARCH D',
    ].contains(prefs['page_size'])) {
      pageSize = prefs['page_size'] as String;
    }
    if (['landscape', 'portrait'].contains(prefs['orientation'])) {
      orientation = prefs['orientation'] as String;
    }
    if (['fit', 'actual', 'custom'].contains(prefs['scale_mode'])) {
      scaleMode = prefs['scale_mode'] as String;
    }
    denominator.text = '${prefs['scale_denominator'] ?? 1}';
    openAfter = prefs['open_after_export'] != false;
  }

  @override
  void dispose() {
    denominator.dispose();
    super.dispose();
  }

  void invalidate() {
    preview = null;
    savedPath = null;
    message = 'Settings changed. Prepare the PDF again before saving.';
  }

  Future<void> prepare() async {
    setState(() {
      working = true;
      preview = null;
      savedPath = null;
    });
    try {
      if (selected.isEmpty) {
        throw const FormatException('Select at least one plate.');
      }
      final value = scaleMode == 'custom'
          ? double.tryParse(denominator.text.trim())
          : 1.0;
      if (value == null || !value.isFinite || value <= 0) {
        throw const FormatException(
          'Enter a positive finite scale denominator.',
        );
      }
      final result = await m.request('design.shop_pdf_prepare', {
        'plate_numbers': selected.toList()..sort(),
        'page_size': pageSize,
        'orientation': orientation,
        'scale_mode': scaleMode,
        'scale_denominator': value,
      });
      if (!mounted) return;
      setState(() {
        if (result['ok'] != true) {
          message = result['error'].toString();
          return;
        }
        preview = result['preview'] as Json;
        message =
            'PDF prepared. Review the page scales and notices, then Save PDF.';
      });
    } catch (e) {
      if (mounted) setState(() => message = e.toString());
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  Future<void> save() async {
    setState(() => working = true);
    try {
      final path = await platform.invokeMethod<String>('pdf');
      if (path == null) return;
      final result = await m.request('design.shop_pdf_export', {
        'token': preview!['token'],
        'path': path,
      });
      if (!mounted) return;
      if (result['ok'] != true) {
        setState(() {
          preview = null;
          message = result['error'].toString();
        });
        return;
      }
      savedPath = (result['output'] as Json)['path'] as String;
      await m.savePreferences({
        ...m.preferences,
        'shopDrawing': {
          ...preview!['settings'] as Json,
          'open_after_export': openAfter,
        },
      });
      var note =
          'Saved ${(preview!['pages'] as List).length}-page shop drawing: $savedPath';
      if (openAfter) {
        try {
          await platform.invokeMethod<void>('openPdf', savedPath);
        } catch (e) {
          note += '\nPDF saved, but the viewer could not open: $e';
        }
      }
      if (!mounted) return;
      setState(() => message = note);
      m.message = note;
    } catch (e) {
      if (mounted) setState(() => message = e.toString());
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  Widget choice(
    String label,
    String value,
    List<String> values,
    void Function(String) change,
  ) => Padding(
    padding: const EdgeInsets.only(bottom: 12),
    child: DropdownButtonFormField<String>(
      initialValue: value,
      decoration: InputDecoration(labelText: label),
      items: values
          .map(
            (v) => DropdownMenuItem(
              value: v,
              child: Text(
                v == 'fit'
                    ? 'Fit to page'
                    : v == 'actual'
                    ? 'Actual size (1:1)'
                    : v == 'custom'
                    ? 'Custom (1:N)'
                    : v,
              ),
            ),
          )
          .toList(),
      onChanged: working
          ? null
          : (v) => setState(() {
              change(v!);
              invalidate();
            }),
    ),
  );
  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !working,
    child: AlertDialog(
      title: const Text('Shop drawing PDF'),
      content: SizedBox(
        width: 620,
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text(
                'One page per selected plate. Fit never enlarges beyond 1:1; actual and custom scales refuse clipping. Print the saved PDF at 100%.',
              ),
              const SizedBox(height: 8),
              for (final p in m.plates)
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  title: Text(
                    'Plate ${p['number']} · ${p['width']} × ${p['height']} ${m.units}',
                  ),
                  value: selected.contains(p['number']),
                  onChanged: working
                      ? null
                      : (v) => setState(() {
                          v == true
                              ? selected.add(p['number'] as int)
                              : selected.remove(p['number']);
                          invalidate();
                        }),
                ),
              choice('Paper size', pageSize, [
                'Letter',
                'Tabloid',
                'ARCH C',
                'ARCH D',
              ], (v) => pageSize = v),
              choice('Orientation', orientation, [
                'landscape',
                'portrait',
              ], (v) => orientation = v),
              choice('Scale', scaleMode, [
                'fit',
                'actual',
                'custom',
              ], (v) => scaleMode = v),
              if (scaleMode == 'custom')
                TextField(
                  controller: denominator,
                  enabled: !working,
                  decoration: const InputDecoration(
                    labelText: 'Custom denominator 1:',
                  ),
                  onChanged: (_) => setState(invalidate),
                ),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                title: const Text('Open PDF after saving'),
                value: openAfter,
                onChanged: working
                    ? null
                    : (v) => setState(() => openAfter = v!),
              ),
              const Text(
                'Layer visibility does not filter printed geometry. Hidden annotations and geometry outside the selected plates are omitted. Dimensions remain annotations; no toolpaths are generated.',
                style: TextStyle(fontSize: 12),
              ),
              const SizedBox(height: 12),
              Text(message),
              if (preview != null) ...[
                for (final page in (preview!['pages'] as List).cast<Json>())
                  Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Text(
                      '${page['label']} · ${page['page_width_in']} × ${page['page_height_in']} in paper · Scale 1:${number(page['scale_denominator']).toStringAsPrecision(5)}',
                    ),
                  ),
                if (number(preview!['omittedCount']) > 0)
                  Text(
                    'Omitted: ${preview!['omittedCount']} object(s) outside the selected pages, including ${preview!['crossingCount']} crossing a plate boundary.',
                  ),
                if (number(preview!['unresolvedCount']) > 0)
                  Text(
                    'Warning: ${preview!['unresolvedCount']} unresolved dimension reference(s). Verify before fabrication.',
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
              ],
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: working ? null : () => Navigator.pop(context),
          child: Text(savedPath == null ? 'Cancel' : 'Done'),
        ),
        OutlinedButton(
          onPressed: working ? null : prepare,
          child: Text(working ? 'Working…' : 'Prepare PDF'),
        ),
        FilledButton(
          onPressed: working || preview == null ? null : save,
          child: const Text('Save PDF…'),
        ),
      ],
    ),
  );
}
