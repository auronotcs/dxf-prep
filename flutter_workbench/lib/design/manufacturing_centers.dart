part of 'manufacturing_workspace.dart';

extension _SmallCenters on _ManufacturingWorkspaceState {
  Future<void> smallCenters(Json op) async {
    Json info;
    try {
      final response = await m.request('manufacturing.small_centers', {
        'id': op['id'],
      });
      if (response['ok'] != true) throw StateError('${response['error']}');
      info = response['manufacturing'] as Json;
    } catch (e) {
      if (mounted) refreshSpecial(() => error = '$e');
      return;
    }
    if (!mounted) return;
    final selected = Set<String>.from(info['selected']);
    final revision = m.state['revision'];
    final accepted = await showDialog<bool>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, update) => AlertDialog(
          title: const Text('Small-center cleanout'),
          content: SizedBox(
            width: 640,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Clear selected enclosed centers before this profile, using its cutter (${info['diameter']} ${info['units']}), depth, stepdown and feeds. The core uses a 3° ramp and 50% stepover. Larger centers and protected artwork require Cleanout.',
                  ),
                  for (final row in info['rows'] as List)
                    CheckboxListTile(
                      value: selected.contains(row['id']),
                      onChanged: row['eligible'] != true
                          ? null
                          : (v) => update(() {
                              if (v == true) {
                                selected.add(row['id']);
                              } else {
                                selected.remove(row['id']);
                              }
                            }),
                      title: Text('${row['kind']} · ${shortId(row['id'])}'),
                      subtitle: Text(
                        row['eligible'] == true
                            ? '${row['loops']} center-out loops'
                            : '${row['reason']}',
                      ),
                    ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () async {
                final picks = await showDialog<Json>(
                  context: context,
                  builder: (_) => ManufacturingPicker(
                    model: m,
                    info: {
                      'kind': 'centers',
                      'shape': <String, dynamic>{},
                      'fingerprint': info['fingerprint'],
                      'operation': {
                        ...op,
                        'small_center_cleanout_ids': selected.toList(),
                      },
                      'eligible_ids': [
                        for (final row in info['rows'] as List)
                          if (row['eligible'] == true) row['id'],
                      ],
                    },
                  ),
                );
                if (context.mounted && picks != null) {
                  update(() {
                    selected.clear();
                    selected.addAll(List<String>.from(picks['ids']));
                  });
                }
              },
              child: const Text('Pick in drawing'),
            ),
            TextButton(
              onPressed: () => update(selected.clear),
              child: const Text('Turn off all'),
            ),
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Preview selected centers'),
            ),
          ],
        ),
      ),
    );
    if (!mounted || accepted != true) return;
    if (m.state['revision'] != revision) {
      refreshSpecial(
        () => error = 'Drawing changed; reopen small-center review.',
      );
      return;
    }
    await request('management_prepare', {
      'action': 'small_centers',
      'params': {
        'id': op['id'],
        'ids': selected.toList(),
        'fingerprint': info['fingerprint'],
      },
    });
  }
}
