import 'package:flutter/material.dart';
import 'controller.dart';

/// Displays engine measurements without changing selection or document state.
class GeometryInspector extends StatefulWidget {
  const GeometryInspector({
    super.key,
    required this.model,
    required this.onAction,
  });
  final DesignController model;
  final void Function(String) onAction;
  @override
  State<GeometryInspector> createState() => _GeometryInspectorState();
}

class _GeometryInspectorState extends State<GeometryInspector> {
  int shown = 20;
  DesignController get m => widget.model;
  String n(dynamic value) =>
      value is num && value.isFinite ? value.toStringAsFixed(4) : '—';
  String distance(dynamic value) =>
      value == null ? 'Not applicable' : '${n(value)} ${m.units}';
  String coordinate(dynamic value) =>
      value is Map ? '${n(value['x'])}, ${n(value['y'])} ${m.units}' : '—';
  Widget field(String label, String value) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 4),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Flexible(
          flex: 4,
          child: Text(
            label,
            style: const TextStyle(fontSize: 11, color: Colors.grey),
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          flex: 6,
          child: SelectableText(
            value,
            textAlign: TextAlign.right,
            style: const TextStyle(fontSize: 11),
          ),
        ),
      ],
    ),
  );
  Widget note(String value) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 6),
    child: Text(value, style: const TextStyle(fontSize: 11, height: 1.4)),
  );
  List<Widget> extent(dynamic value) {
    if (value is! List || value.length != 4) return [];
    return [
      field('Min X / Y', '${n(value[0])}, ${n(value[1])} ${m.units}'),
      field('Max X / Y', '${n(value[2])}, ${n(value[3])} ${m.units}'),
      field('Width', distance(number(value[2]) - number(value[0]))),
      field('Height', distance(number(value[3]) - number(value[1]))),
    ];
  }

  List<Widget> details(Json e, Json? measurement) => [
    field('Layer', e['layer'] ?? '0'),
    field('Visibility', m.visible(e) ? 'Visible' : 'Hidden'),
    field(
      'Editing',
      m.readOnly
          ? 'View-only drawing'
          : m.locked(e)
          ? 'Layer locked'
          : !m.visible(e)
          ? 'Hidden object'
          : 'Editable',
    ),
    if (e['kind'] == 'line') ...[
      field('Start X / Y', coordinate(e['start'])),
      field('End X / Y', coordinate(e['end'])),
    ],
    if (e['kind'] == 'circle') ...[
      field('Center X / Y', coordinate(e['center'])),
      field('Radius', distance(e['radius'])),
      field('Diameter', distance(number(e['radius']) * 2)),
    ],
    if (e['kind'] == 'rectangle') ...[
      field('Corner A', coordinate(e['corner_a'])),
      field('Corner B', coordinate(e['corner_b'])),
    ],
    if (e['kind'] == 'polyline') ...[
      field('Contour', e['closed'] == true ? 'Closed' : 'Open'),
      field('Vertices', '${(e['points'] as List? ?? []).length}'),
      if ((e['points'] as List? ?? []).isNotEmpty) ...[
        field('First X / Y', coordinate(e['points'][0])),
        field('Last X / Y', coordinate((e['points'] as List).last)),
      ],
    ],
    if (e['kind'] == 'text') ...[
      field('Text', e['text'] ?? ''),
      field('Insertion X / Y', coordinate(e['insertion'])),
      field('Text height', distance(e['height'])),
      field('Rotation', '${n(e['rotation'])}°'),
      field('Font', e['font_family'] ?? '—'),
      field('Alignment', e['alignment'] ?? '—'),
      note(
        'Editable text: bounds are approximate. No cutting length or area until converted to curves.',
      ),
    ],
    ...extent(e['bounds']),
    if (measurement?['length'] != null)
      field(
        measurement!['closed'] == true ? 'Perimeter' : 'Length',
        distance(measurement['length']),
      ),
    if (measurement?['area'] != null)
      field('Contour area', '${n(measurement!['area'])} ${m.units}²'),
    if (e['kind'] == 'polyline' && e['closed'] == true)
      note(
        'Contour area uses the core polygon measurement; self-intersections can cancel.',
      ),
    field('Object ID', e['id'] ?? '—'),
  ];
  @override
  Widget build(BuildContext context) {
    final selected = m.entities
        .where((e) => m.selection.contains(e['id']))
        .toList();
    if (selected.isEmpty) {
      return note(
        'Click geometry or drag a selection box. Hold Shift to extend the selection.',
      );
    }
    final inspection = m.state['selectionInspection'] as Json?;
    final measurements = {
      for (final item in inspection?['items'] as List? ?? [])
        item['id']: item as Json,
    };
    final editable =
        m.ready &&
        !m.busy &&
        !m.readOnly &&
        selected.every((e) => m.visible(e) && !m.locked(e));
    Widget action(String id, String label) => TextButton(
      onPressed: editable ? () => widget.onAction(id) : null,
      child: Text(label),
    );
    final single = selected.length == 1 ? selected.single : null;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (single != null) ...[
          Text(
            '${single['kind']}'.toUpperCase(),
            style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
          ),
          ...details(single, measurements[single['id']]),
        ] else ...[
          Text(
            'Selection totals · ${selected.length} objects',
            style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
          ),
          field('Types', selected.map((e) => e['kind']).toSet().join(', ')),
          field('Layers', selected.map((e) => e['layer']).toSet().join(', ')),
          ...extent(m.state['selectionBounds']),
          if (inspection?['totalLength'] != null)
            field('Total contour length', distance(inspection!['totalLength'])),
          if (inspection?['contourAreaSum'] != null)
            field(
              'Sum of contour areas',
              '${n(inspection!['contourAreaSum'])} ${m.units}²',
            ),
          if (inspection != null)
            note(
              'Length: ${inspection['lengthCount']} objects. Area: ${inspection['areaCount']} closed contours. Text excluded. Areas are summed individually; overlaps and holes are not subtracted.',
            ),
          if (selected.any((e) => e['kind'] == 'text'))
            note('Selection bounds include approximate text extents.'),
          for (final e in selected.take(shown))
            ExpansionTile(
              key: ValueKey('inspect-${e['id']}'),
              tilePadding: EdgeInsets.zero,
              title: Text(
                '${e['kind']} · ${e['layer']}',
                style: const TextStyle(fontSize: 12),
              ),
              subtitle: Text(
                '${e['id']}',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 10),
              ),
              children: details(e, measurements[e['id']]),
            ),
          if (selected.length > shown)
            TextButton(
              onPressed: () => setState(() => shown += 20),
              child: Text('Show more (${selected.length - shown} remaining)'),
            ),
        ],
        if (inspection == null)
          note('Save and restart Flutter to load detailed core measurements.'),
        if (!editable)
          note(
            m.readOnly
                ? 'This drawing is view-only.'
                : selected.any((e) => !m.visible(e) || m.locked(e))
                ? 'Unlock and show all selected objects before editing.'
                : 'Editing is unavailable while the drawing is busy.',
          ),
        Wrap(
          children: [
            action('move', 'Move / rotate'),
            action('scale', 'Scale'),
            action('duplicate', 'Duplicate'),
            action('appearance', 'Fill / stroke'),
            if (selected.every((e) => e['kind'] == 'circle'))
              action('resizeCircles', 'Resize circles'),
            if (single?['kind'] == 'text') action('editText', 'Edit text'),
            if (single != null &&
                ['line', 'rectangle', 'polyline'].contains(single['kind']))
              action('points', 'Edit points'),
            if (single?['kind'] == 'polyline')
              action('pointCoordinates', 'Point coordinates'),
          ],
        ),
      ],
    );
  }
}
