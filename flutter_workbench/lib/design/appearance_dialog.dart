import 'dart:convert';
import 'package:flutter/material.dart';
import 'controller.dart';
import 'palette_dialog.dart';

Color previewColor(dynamic value, [Color fallback = Colors.black]) {
  final rgb = value is Map ? value['preview_rgb'] : null;
  return rgb is List && rgb.length == 3
      ? Color.fromARGB(
          255,
          (rgb[0] as num).toInt(),
          (rgb[1] as num).toInt(),
          (rgb[2] as num).toInt(),
        )
      : fallback;
}

class ColorDraft {
  ColorDraft(this.original)
    : model = original['model'] as String,
      channels = TextEditingController(
        text: (original['channels'] as List).join(', '),
      ),
      name = TextEditingController(text: original['name'] ?? ''),
      profile = TextEditingController(text: original['profile'] ?? ''),
      preview = TextEditingController(
        text:
            '#${(original['preview_rgb'] as List).map((v) => (v as num).toInt().toRadixString(16).padLeft(2, '0')).join()}',
      );
  Json original;
  String? paletteToken;
  String model;
  final TextEditingController channels, name, profile, preview;
  void usePalette(Json response) {
    original = Map<String, dynamic>.from(response['color'] as Map);
    paletteToken = response['token'] as String;
    model = original['model'] as String;
    channels.text = (original['channels'] as List).join(', ');
    name.text = original['name'] ?? '';
    profile.text = original['profile'] ?? '';
    preview.text =
        '#${(original['preview_rgb'] as List).map((v) => (v as num).toInt().toRadixString(16).padLeft(2, '0')).join()}';
  }

  String? get activePaletteToken =>
      jsonEncode(value()) == jsonEncode(original) ? paletteToken : null;
  Json value() {
    final values = channels.text
        .split(',')
        .where((v) => v.trim().isNotEmpty)
        .map((v) => double.parse(v.trim()))
        .toList();
    final count = model == 'rgb'
        ? 3
        : model == 'cmyk'
        ? 4
        : 0;
    final limit = model == 'cmyk' ? 100 : 255;
    if (values.length != count ||
        values.any((v) => !v.isFinite || v < 0 || v > limit)) {
      throw FormatException(
        '$model requires $count channels between 0 and $limit.',
      );
    }
    List<int> rgb;
    if (model == 'named') {
      if (name.text.trim().isEmpty ||
          !RegExp(r'^#[0-9a-fA-F]{6}$').hasMatch(preview.text.trim())) {
        throw const FormatException(
          'Named colors need a name and a #RRGGBB preview.',
        );
      }
      rgb = [
        for (var i = 1; i < 7; i += 2)
          int.parse(preview.text.trim().substring(i, i + 2), radix: 16),
      ];
    } else if (model == 'rgb') {
      rgb = values.map((v) => v.round()).toList();
    } else {
      rgb = [
        for (var i = 0; i < 3; i++)
          (255 * (1 - values[i] / 100) * (1 - values[3] / 100)).round(),
      ];
    }
    // Preserve authored channel precision and palette provenance if untouched.
    if (model == original['model'] &&
        jsonEncode(values) ==
            jsonEncode(
              (original['channels'] as List)
                  .map((v) => (v as num).toDouble())
                  .toList(),
            ) &&
        name.text == (original['name'] ?? '') &&
        profile.text == (original['profile'] ?? '') &&
        (model != 'named' ||
            jsonEncode(rgb) == jsonEncode(original['preview_rgb']))) {
      return Map<String, dynamic>.from(original);
    }
    return {
      'model': model,
      'channels': values,
      'preview_rgb': rgb,
      'name': name.text.trim(),
      'profile': profile.text.trim(),
    };
  }

  void dispose() {
    for (final field in [channels, name, profile, preview]) {
      field.dispose();
    }
  }
}

class AppearanceDialog extends StatefulWidget {
  const AppearanceDialog({super.key, required this.model});
  final DesignController model;
  @override
  State<AppearanceDialog> createState() => _AppearanceDialogState();
}

class _AppearanceDialogState extends State<AppearanceDialog> {
  late final ColorDraft fill, stroke;
  late final TextEditingController width;
  late final int revision;
  late final List<String> ids;
  late bool fillEnabled, strokeEnabled, mixed;
  String? error;
  DesignController get m => widget.model;
  @override
  void initState() {
    super.initState();
    revision = m.state['revision'] as int;
    final selected = m.entities
        .where((e) => m.selection.contains(e['id']))
        .toList();
    ids = selected.map((e) => e['id'] as String).toList();
    final first = selected.first;
    mixed = selected.any(
      (e) => jsonEncode(e['appearance']) != jsonEncode(first['appearance']),
    );
    final layer = m.layers
        .where((l) => l['name'] == first['layer'])
        .firstOrNull;
    final hex = (layer?['color'] ?? '#202830').toString().replaceFirst('#', '');
    final ink = RegExp(r'^[0-9a-fA-F]{6}$').hasMatch(hex)
        ? [
            for (var i = 0; i < 6; i += 2)
              int.parse(hex.substring(i, i + 2), radix: 16),
          ]
        : [32, 40, 48];
    final initial =
        first['appearance'] as Json? ??
        {
          'fill_enabled': false,
          'stroke_enabled': true,
          'stroke_width': 0,
          'fill': {
            'model': 'rgb',
            'channels': [0, 0, 0],
            'preview_rgb': [0, 0, 0],
          },
          'stroke': {'model': 'rgb', 'channels': ink, 'preview_rgb': ink},
        };
    fill = ColorDraft(Map<String, dynamic>.from(initial['fill'] as Map));
    stroke = ColorDraft(Map<String, dynamic>.from(initial['stroke'] as Map));
    fillEnabled = initial['fill_enabled'] == true;
    strokeEnabled = initial['stroke_enabled'] == true;
    width = TextEditingController(text: initial['stroke_width'].toString());
    m.addListener(changed);
  }

  void changed() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    m.removeListener(changed);
    fill.dispose();
    stroke.dispose();
    width.dispose();
    super.dispose();
  }

  Future<void> apply({bool inherit = false}) async {
    try {
      final size = inherit ? 0.0 : double.parse(width.text);
      if (!inherit && (!size.isFinite || size < 0)) {
        throw const FormatException('Stroke width must be zero or positive.');
      }
      final ok = await m.run('design.appearance', {
        'expected_revision': revision,
        'entity_ids': ids,
        'inherit_layer': inherit,
        if (!inherit)
          'palette_tokens': {
            if (fill.activePaletteToken != null)
              'fill': fill.activePaletteToken,
            if (stroke.activePaletteToken != null)
              'stroke': stroke.activePaletteToken,
          },
        if (!inherit)
          'appearance': {
            'fill_enabled': fillEnabled,
            'fill': fill.value(),
            'stroke_enabled': strokeEnabled,
            'stroke': stroke.value(),
            'stroke_width': size,
          },
      });
      if (!mounted) return;
      if (ok) {
        Navigator.pop(context);
      } else {
        setState(() => error = m.message);
      }
    } on FormatException catch (e) {
      setState(() => error = e.message);
    }
  }

  Widget colorControls(
    String label,
    ColorDraft draft,
    bool enabled,
    ValueChanged<bool> toggle,
  ) {
    Color color = Colors.transparent;
    try {
      color = previewColor(draft.value());
    } on FormatException {
      /* Invalid input remains editable. */
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Checkbox(
              value: enabled,
              onChanged: (v) => setState(() => toggle(v!)),
            ),
            Text(label),
            const SizedBox(width: 12),
            Container(
              width: 35,
              height: 20,
              decoration: BoxDecoration(
                color: color,
                border: Border.all(color: Colors.grey),
              ),
            ),
            const SizedBox(width: 12),
            DropdownButton<String>(
              value: draft.model,
              items: [
                for (final model in ['rgb', 'cmyk', 'named'])
                  DropdownMenuItem(
                    value: model,
                    child: Text(model.toUpperCase()),
                  ),
              ],
              onChanged: (value) => setState(() {
                draft.model = value!;
                draft.channels.text = value == 'cmyk'
                    ? '0, 0, 0, 100'
                    : value == 'rgb'
                    ? '0, 0, 0'
                    : '';
              }),
            ),
            if ((m.state['capabilities'] as List? ?? []).contains(
              'color_palettes',
            ))
              TextButton(
                onPressed: m.busy
                    ? null
                    : () async {
                        final response = await showDialog<Json>(
                          context: context,
                          builder: (_) => PaletteDialog(model: m),
                        );
                        if (!mounted || response == null) return;
                        setState(() {
                          draft.usePalette(response);
                          toggle(true);
                        });
                      },
                child: const Text('Palette…'),
              ),
          ],
        ),
        if (draft.model != 'named')
          TextField(
            key: ValueKey('$label-channels'),
            controller: draft.channels,
            decoration: InputDecoration(
              labelText:
                  '$label channels (${draft.model == 'rgb' ? 'R, G, B · 0–255' : 'C, M, Y, K · 0–100'})',
            ),
            onChanged: (_) => setState(() {}),
          ),
        if (draft.model == 'named')
          TextField(
            key: ValueKey('$label-preview'),
            controller: draft.preview,
            decoration: const InputDecoration(
              labelText: 'Preview RGB (#RRGGBB)',
            ),
            onChanged: (_) => setState(() {}),
          ),
        const SizedBox(height: 8),
        Row(
          children: [
            Expanded(
              child: TextField(
                controller: draft.name,
                decoration: InputDecoration(
                  labelText: draft.model == 'named'
                      ? 'Swatch name'
                      : 'Color name (optional)',
                ),
                onChanged: (_) => setState(() {}),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: TextField(
                controller: draft.profile,
                decoration: const InputDecoration(
                  labelText: 'Profile / source (optional)',
                ),
                onChanged: (_) => setState(() {}),
              ),
            ),
          ],
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Object Fill / Stroke'),
    content: SizedBox(
      width: 560,
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              '${ids.length} object(s)${mixed ? ' · Mixed appearances; applying replaces all with these settings.' : ''}',
            ),
            colorControls('Fill', fill, fillEnabled, (v) => fillEnabled = v),
            const SizedBox(height: 12),
            colorControls(
              'Stroke',
              stroke,
              strokeEnabled,
              (v) => strokeEnabled = v,
            ),
            const SizedBox(height: 12),
            TextField(
              key: const ValueKey('stroke-width'),
              controller: width,
              decoration: InputDecoration(
                labelText: 'Stroke width (${m.units}); 0 = hairline',
              ),
            ),
            const SizedBox(height: 10),
            const Text(
              'CMYK channels are stored exactly. Screen colors are approximate previews. Existing palette references are retained when unchanged.',
              style: TextStyle(fontSize: 11),
            ),
            if (error != null)
              Padding(
                padding: const EdgeInsets.only(top: 10),
                child: Text(
                  error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ),
          ],
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: m.busy || m.readOnly ? null : () => apply(inherit: true),
        child: const Text('Use layer appearance'),
      ),
      TextButton(
        onPressed: m.busy ? null : () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed: m.busy || m.readOnly ? null : apply,
        child: const Text('Apply'),
      ),
    ],
  );
}
