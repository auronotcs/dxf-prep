part of 'manufacturing_workspace.dart';

extension _KeepoutManufacturing on _ManufacturingWorkspaceState {
  String keepoutBounds(Json zone) {
    final points = (zone['points'] as List).map(point).toList();
    if (points.isEmpty) return 'Empty outline';
    final x = points.map((p) => p.dx), y = points.map((p) => p.dy);
    String n(double v) => v.toStringAsFixed(3);
    return 'X ${n(x.reduce(math.min))}–${n(x.reduce(math.max))}, Y ${n(y.reduce(math.min))}–${n(y.reduce(math.max))} ${m.units}';
  }

  Future<void> keepoutsDialog() async {
    if (data['keepoutsEditable'] != true) {
      await showDialog<void>(
        context: context,
        builder: (c) => AlertDialog(
          title: const Text('Fixtures / holes / keepouts'),
          content: const Text(
            'This document is using an earlier backend. Keep it open to preserve unsaved work. New document tabs use the updated tools; save and reopen this document when you are ready.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(c),
              child: const Text('Close'),
            ),
          ],
        ),
      );
      return;
    }
    final startRevision = m.state['revision'];
    final zones = (data['keepouts'] as List? ?? []).cast<Json>();
    final choice = await showDialog<Json>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Fixtures / holes / keepouts'),
        content: SizedBox(
          width: 620,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Drawing coordinates in ${m.units}. Required clearance Z is measured above the stock top and must include your clearance margin.',
                ),
                const SizedBox(height: 8),
                const Text(
                  'Checks include cutter and configured holder envelopes. Polygon bounds and full-circle arc bounds are conservative. These zones do not reroute motion or raise Safe Z.',
                ),
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    FilledButton.tonal(
                      onPressed: () => Navigator.pop(c, <String, dynamic>{
                        'change': 'add',
                        'shape': 'rectangle',
                      }),
                      child: const Text('Add rectangle'),
                    ),
                    FilledButton.tonal(
                      onPressed: m.selection.length == 1
                          ? () => Navigator.pop(c, <String, dynamic>{
                              'change': 'add',
                              'shape': 'selection',
                              'entity_id': m.selection.single,
                            })
                          : null,
                      child: const Text('From selected contour'),
                    ),
                  ],
                ),
                for (final issue in (data['holeLinkIssues'] as List? ?? []))
                  Text('$issue', style: const TextStyle(color: Colors.red)),
                if (data['holeExclusions'] == true)
                  FilledButton.tonal(
                    onPressed: () => Navigator.pop(c, <String, dynamic>{
                      'change': 'add',
                      'shape': 'rectangle',
                      'kind': 'hole_exclusion',
                    }),
                    child: const Text('Add hole / damaged area'),
                  ),
                if (zones.isEmpty)
                  const Padding(
                    padding: EdgeInsets.all(16),
                    child: Text('No keepouts defined.'),
                  ),
                for (final z in zones)
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(10),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            '${z['name']} · ${z['enabled'] == false ? 'Disabled' : 'Enabled'}',
                            style: const TextStyle(fontWeight: FontWeight.bold),
                          ),
                          Text(
                            'Required clearance Z: ${z['clearance_z']} ${m.units} · ${(z['points'] as List).length} vertices',
                          ),
                          if (z['kind'] == 'hole_exclusion')
                            Text(
                              'Hole margin: ${z['extra_margin'] ?? 0} ${m.units} · ${z['patchwork_id'] == null ? 'Fixed in drawing' : 'Linked piece: ${z['patchwork_id']}'}',
                            ),
                          Wrap(
                            spacing: 8,
                            children: [
                              TextButton(
                                onPressed: () =>
                                    Navigator.pop(c, <String, dynamic>{
                                      'change': 'edit',
                                      'zone': z,
                                    }),
                                child: const Text('Edit / move'),
                              ),
                              if (z['kind'] == 'hole_exclusion')
                                TextButton(
                                  onPressed: () =>
                                      Navigator.pop(c, <String, dynamic>{
                                        'change': 'link',
                                        'zone': z,
                                      }),
                                  child: const Text('Link / detach piece'),
                                ),
                              TextButton(
                                onPressed: () =>
                                    Navigator.pop(c, <String, dynamic>{
                                      'change': 'toggle',
                                      'id': z['id'],
                                      'enabled': z['enabled'] == false,
                                    }),
                                child: Text(
                                  z['enabled'] == false ? 'Enable' : 'Disable',
                                ),
                              ),
                              TextButton(
                                onPressed: () =>
                                    Navigator.pop(c, <String, dynamic>{
                                      'change': 'delete',
                                      'id': z['id'],
                                    }),
                                child: const Text('Delete'),
                              ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c),
            child: const Text('Close'),
          ),
        ],
      ),
    );
    if (!mounted || choice == null || m.state['revision'] != startRevision) {
      return;
    }
    Json? params = choice;
    if (choice['change'] == 'link') {
      params = await holeLinkEditor(choice['zone'] as Json);
    }
    if (choice['change'] == 'add' || choice['change'] == 'edit') {
      params = await keepoutEditor(choice);
    }
    if (!mounted || params == null || m.state['revision'] != startRevision) {
      return;
    }
    await request('management_prepare', {
      'action': 'keepouts',
      'params': params,
    });
  }

  Future<Json?> keepoutEditor(Json choice) async {
    final zone = choice['zone'] as Json?;
    final hole = (zone?['kind'] ?? choice['kind']) == 'hole_exclusion';
    int? plate = m.plates.isEmpty ? null : m.plates.first['number'] as int;
    final key = GlobalKey<FormState>();
    var shape = zone == null ? choice['shape'] as String : 'existing';
    final fields = <String, TextEditingController>{
      'name': TextEditingController(
        text:
            zone?['name'] as String? ??
            (hole ? 'Hole / damaged area' : 'Clamp'),
      ),
      'clearance_z': TextEditingController(
        text: '${zone?['clearance_z'] ?? .5 * unitFactor}',
      ),
      'extra_margin': TextEditingController(
        text: '${zone?['extra_margin'] ?? 0}',
      ),
      'x': TextEditingController(text: '0'),
      'y': TextEditingController(text: '0'),
      'width': TextEditingController(text: '${2 * unitFactor}'),
      'height': TextEditingController(text: '$unitFactor'),
      'dx': TextEditingController(text: '0'),
      'dy': TextEditingController(text: '0'),
    };
    if (zone != null) {
      final pts = (zone['points'] as List).map(point).toList();
      if (pts.isNotEmpty) {
        final xs = pts.map((p) => p.dx), ys = pts.map((p) => p.dy);
        final x = xs.reduce(math.min), y = ys.reduce(math.min);
        fields['x']!.text = '$x';
        fields['y']!.text = '$y';
        fields['width']!.text = '${xs.reduce(math.max) - x}';
        fields['height']!.text = '${ys.reduce(math.max) - y}';
      }
    }
    const labels = {
      'extra_margin': 'Extra exclusion margin',
      'name': 'Name',
      'clearance_z': 'Required clearance Z',
      'x': 'X',
      'y': 'Y',
      'width': 'Width',
      'height': 'Height',
      'dx': 'Move X',
      'dy': 'Move Y',
    };
    final result = await showDialog<Json>(
      context: context,
      builder: (c) => StatefulBuilder(
        builder: (c, update) => AlertDialog(
          title: Text(zone == null ? 'Add keepout' : 'Edit keepout'),
          content: SizedBox(
            width: 460,
            child: SingleChildScrollView(
              child: Form(
                key: key,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      'All distances in ${m.units}; Z is above the stock top. Include your clearance margin.',
                    ),
                    if (hole && zone == null)
                      DropdownButtonFormField<int>(
                        initialValue: plate,
                        decoration: const InputDecoration(
                          labelText:
                              'Stock plate (X/Y are drawing coordinates)',
                        ),
                        items: [
                          for (final p in m.plates)
                            DropdownMenuItem(
                              value: p['number'] as int,
                              child: Text('Plate ${p['number']}'),
                            ),
                        ],
                        onChanged: (v) => update(() => plate = v),
                        validator: (v) => v == null ? 'Choose a plate.' : null,
                      ),
                    if (zone != null)
                      DropdownButtonFormField<String>(
                        initialValue: shape,
                        decoration: const InputDecoration(labelText: 'Outline'),
                        items: const [
                          DropdownMenuItem(
                            value: 'existing',
                            child: Text('Keep outline / move'),
                          ),
                          DropdownMenuItem(
                            value: 'rectangle',
                            child: Text('Replace with rectangle'),
                          ),
                        ],
                        onChanged: (v) => update(() => shape = v!),
                      ),
                    if (shape == 'selection')
                      const Text(
                        'Copies the selected outline; later drawing edits do not move the keepout.',
                      ),
                    for (final id in [
                      'name',
                      'clearance_z',
                      if (hole) 'extra_margin',
                      if (shape == 'rectangle') ...[
                        'x',
                        'y',
                        'width',
                        'height',
                      ],
                      if (zone != null && shape == 'existing') ...['dx', 'dy'],
                    ])
                      Padding(
                        padding: const EdgeInsets.only(top: 8),
                        child: TextFormField(
                          controller: fields[id],
                          decoration: InputDecoration(labelText: labels[id]),
                          validator: (value) {
                            if (id == 'name') {
                              return value == null ||
                                      value.trim().isEmpty ||
                                      value.trim().length > 120
                                  ? 'Enter a name (1–120 characters).'
                                  : null;
                            }
                            final n = double.tryParse(value ?? '');
                            if (n == null || !n.isFinite) {
                              return 'Enter a finite number.';
                            }
                            if ([
                                  'clearance_z',
                                  'width',
                                  'height',
                                ].contains(id) &&
                                n <= 0) {
                              return 'Must be greater than zero.';
                            }
                            if (id == 'extra_margin' && n < 0) {
                              return 'Must be nonnegative.';
                            }
                            return null;
                          },
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(c),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () {
                if (!key.currentState!.validate()) return;
                Navigator.pop(c, <String, dynamic>{
                  'change': choice['change'],
                  if (hole) 'kind': 'hole_exclusion',
                  if (hole)
                    'extra_margin': double.parse(fields['extra_margin']!.text),
                  if (hole && zone == null) 'plate_number': plate,
                  if (zone != null) 'id': zone['id'],
                  'shape': shape,
                  'name': fields['name']!.text.trim(),
                  'clearance_z': double.parse(fields['clearance_z']!.text),
                  if (shape == 'selection') 'entity_id': choice['entity_id'],
                  for (final id in [
                    if (shape == 'rectangle') ...['x', 'y', 'width', 'height'],
                    if (zone != null && shape == 'existing') ...['dx', 'dy'],
                  ])
                    id: double.parse(fields[id]!.text),
                });
              },
              child: const Text('Review keepout'),
            ),
          ],
        ),
      ),
    );
    // Route exit animation can still reference these controllers.
    await Future<void>.delayed(const Duration(milliseconds: 300));
    for (final c in fields.values) {
      c.dispose();
    }
    return result;
  }

  Future<Json?> holeLinkEditor(Json zone) async {
    String selected = zone['patchwork_id'] as String? ?? '';
    final choices = (data['holeLinkChoices'] as List? ?? []).cast<Json>();
    if (!choices.any((p) => p['id'] == selected)) selected = '';
    return showDialog<Json>(
      context: context,
      builder: (c) => StatefulBuilder(
        builder: (c, update) => AlertDialog(
          title: const Text('Link hole to Patchwork piece'),
          content: SizedBox(
            width: 440,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Text(
                  'Linking preserves the current physical position and size. The hole then follows piece translation. Detach keeps the last known position if the parent is missing.',
                ),
                DropdownButtonFormField<String>(
                  initialValue: selected,
                  isExpanded: true,
                  items: [
                    const DropdownMenuItem(
                      value: '',
                      child: Text('Fixed in drawing / detach'),
                    ),
                    for (final p in choices)
                      DropdownMenuItem(
                        value: p['id'] as String,
                        child: Text('${p['name']} · Plate ${p['plate']}'),
                      ),
                  ],
                  onChanged: (v) => update(() => selected = v!),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(c),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(c, <String, dynamic>{
                'change': 'link',
                'id': zone['id'],
                'patchwork_id': selected.isEmpty ? null : selected,
              }),
              child: const Text('Review link'),
            ),
          ],
        ),
      ),
    );
  }

  Widget keepoutReview(Json change) {
    final before = change['before'] as Json?, after = change['after'] as Json?;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          '${change['change'].toString().toUpperCase()}: ${after?['name'] ?? before?['name']}',
          style: const TextStyle(fontWeight: FontWeight.bold),
        ),
        if (change['protection_removed'] == true)
          const Text(
            'This removes keepout protection. Confirm the fixture is absent or the hole / damaged area is safe before Apply.',
            style: TextStyle(color: Colors.deepOrange),
          ),
        if (before != null) ...[
          Text(
            'Before: Z ${before['clearance_z']} ${m.units} · ${before['enabled'] == false ? 'disabled' : 'enabled'}',
          ),
          Text(keepoutBounds(before)),
        ],
        if (after != null) ...[
          Text(
            'After: Z ${after['clearance_z']} ${m.units} · ${after['enabled'] == false ? 'disabled' : 'enabled'} · ${(after['points'] as List).length} vertices',
          ),
          Text(keepoutBounds(after)),
          if (after['kind'] == 'hole_exclusion')
            Text(
              'Hole margin ${after['extra_margin']} ${m.units} · linked piece ${after['patchwork_id'] ?? 'none'}',
            ),
        ],
        const Text(
          'Red: proposed keepouts. Cutter/holder checks use conservative polygon bounds and full-circle arc bounds. No automatic motion rerouting.',
        ),
      ],
    );
  }
}
