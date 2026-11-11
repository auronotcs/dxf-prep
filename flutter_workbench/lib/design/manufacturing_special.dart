part of 'manufacturing_workspace.dart';

const specialNames = {
  'engrave': 'Drag engraving',
  'drag_knife': 'Drag knife',
  'thread_mill': 'Thread milling',
  'rest_machining': 'Pocket rest machining',
  'profile_rest_machining': 'Profile rest machining',
};

extension _SpecialManufacturing on _ManufacturingWorkspaceState {
  List<Json> get ops => (data['operations'] as List? ?? []).cast<Json>();
  List<Json> get tools => (data['tools'] as List? ?? []).cast<Json>();
  Future<void> specialMenu() async {
    final choice = await showDialog<String>(
      context: context,
      builder: (context) => SimpleDialog(
        title: const Text('More Manufacturing tools'),
        children: [
          if (data['knifeWorkflows'] == true)
            SimpleDialogOption(
              onPressed: () => Navigator.pop(context, 'knife_presets'),
              child: const Text('Knife presets'),
            ),
          for (final e in specialNames.entries)
            SimpleDialogOption(
              onPressed: () => Navigator.pop(context, e.key),
              child: Text(e.value),
            ),
          SimpleDialogOption(
            onPressed: () => Navigator.pop(context, 'release'),
            child: const Text('Final tab release'),
          ),
          SimpleDialogOption(
            onPressed: () => Navigator.pop(context, 'inspect'),
            child: const Text('Operation inspector'),
          ),
        ],
      ),
    );
    if (!mounted || choice == null) return;
    if (choice == 'knife_presets') {
      knifePresets();
      return;
    }
    if (choice == 'release') {
      releaseAudit();
      return;
    }
    if (choice == 'inspect') {
      refreshSpecial(() => inspectorOpen = true);
      return;
    }
    specialOperation(choice);
  }

  void specialOperation(String kind, [Json? op, Json? preset]) {
    final f = unitFactor;
    final advancedKnife =
        kind == 'drag_knife' && data['knifeWorkflows'] == true;
    final rest = kind.endsWith('rest_machining');
    final sourceKind = kind == 'rest_machining'
        ? 'island_fill'
        : 'router_offset';
    final sources = ops
        .where(
          (o) =>
              o['kind'] == sourceKind &&
              o['enabled'] != false &&
              ((o['profile_retention'] as Map?)?.isEmpty ?? true),
        )
        .toList();
    if (rest && sources.isEmpty) {
      refreshSpecial(() {
        inspectorOpen = true;
        error =
            'Create an enabled independent ${sourceKind == 'island_fill' ? 'cleanout' : 'profile'} first.';
      });
      return;
    }
    final toolKind = rest
        ? 'end_mill'
        : kind == 'engrave'
        ? 'engraver'
        : kind;
    final candidates = tools.where((t) => t['kind'] == toolKind).toList();
    if (candidates.isEmpty) {
      refreshSpecial(() {
        error = 'Configure a $toolKind cutter in Turret Setup first.';
        inspectorOpen = true;
      });
      return;
    }
    final defaults = <String, dynamic>{
      'name': specialNames[kind],
      'tool_station': candidates.first['station'],
      if (!rest) 'target_depth': (kind == 'engrave' ? .05 : .1) * f,
      'safe_z': .25 * f,
      if (kind == 'engrave') ...{
        'approach_height': .05 * f,
        'reverse_open_paths': false,
        'require_stay_on_material': true,
      },
      if (kind == 'drag_knife') ...{
        'blade_offset': .1 * f,
        'swivel_depth': .005 * f,
        'swivel_angle': 20.0,
        'approach_height': .05 * f,
        'alignment_length': .3 * f,
        'curve_tolerance': .001 * f,
        'passes': 1,
        'reverse_paths': false,
        'setup_verified': false,
        if (advancedKnife) ...{
          'outside_corner_loops': false,
          'loop_radius': .2 * f,
          'loop_feed_percent': 50.0,
          'edge_aware_corners': false,
        },
      },
      if (kind == 'thread_mill') ...{
        'thread_designation': '1/4-20 UNC-2B',
        'pitch_diameter_adjustment': 0.0,
        'radial_passes': 2,
        'spring_passes': 1,
        'handedness': 'right',
        'cut_direction': 'climb',
        'cutter_verified': false,
        'pilot_hole_depth': .2 * f,
        'bottom_clearance': .05 * f,
      },
      if (rest) ...{
        'source_operation_id': sources.first['id'],
        'stepdown': candidates.first['max_stepdown'] ?? .05 * f,
        'stock_to_leave': 0.0,
        'ignore_stock_less_than': 0.0,
      },
      if (kind == 'rest_machining')
        'stepover': number(candidates.first['diameter']) * .4,
      if (kind == 'profile_rest_machining') ...{
        'inherit_pass_schedule': true,
        'preserve_tabs': true,
        'remove_tabs_after': false,
      },
    };
    final values = {
      for (final e in defaults.entries)
        e.key: preset?[e.key] ?? op?[e.key] ?? e.value,
    };
    const knifeRates = {
      'cut_feed',
      'plunge_feed',
      'corner_feed',
      'lead_in_feed',
    };
    if (advancedKnife) {
      final source = preset ?? op ?? <String, dynamic>{};
      for (final e in {
        'cut_feed': 'feed_rate_override',
        'plunge_feed': 'plunge_rate_override',
        'corner_feed': 'corner_feed_ipm',
        'lead_in_feed': 'lead_in_feed_ipm',
      }.entries) {
        final raw = source[e.value];
        values[e.key] = raw == null
            ? null
            : number(raw) * (e.value.endsWith('ipm') ? f : 60);
      }
      if (preset != null) values['setup_verified'] = false;
    }
    final booleans = defaults.entries
        .where((e) => e.value is bool)
        .map((e) => e.key)
        .toSet();
    final numeric = defaults.entries
        .where((e) => e.value is num)
        .map((e) => e.key)
        .toSet();
    form(
      op == null ? 'Add ${specialNames[kind]}' : 'Edit ${specialNames[kind]}',
      'special_save',
      values,
      {...numeric, if (advancedKnife) ...knifeRates},
      optional: advancedKnife ? knifeRates : const {},
      integers: {'tool_station', 'passes', 'radial_passes', 'spring_passes'},
      booleans: booleans,
      choices: {
        'tool_station': [for (final t in candidates) '${t['station']}'],
        if (rest)
          'source_operation_id': [for (final s in sources) s['id'].toString()],
        if (kind == 'thread_mill') ...{
          'thread_designation': ['1/4-20 UNC-2B', '10-24 UNC-2B'],
          'handedness': ['right', 'left'],
          'cut_direction': ['climb', 'conventional'],
        },
      },
      extra: {'kind': kind, if (op != null) 'id': op['id']},
      contours: rest
          ? null
          : m.entities
                .where(
                  (e) =>
                      [
                        'circle',
                        'rectangle',
                        'polyline',
                        'line',
                      ].contains(e['kind']) &&
                      (kind != 'thread_mill' || e['kind'] == 'circle') &&
                      m.visible(e) &&
                      !m.locked(e),
                )
                .toList(),
      selected: preset?['entity_ids'] != null
          ? Set<String>.from(preset!['entity_ids'])
          : op == null
          ? m.selection
          : Set<String>.from(op['entity_ids']),
      note: switch (kind) {
        'engrave' =>
          'Spindle-off spring-loaded drag engraving. Target depth is spring compression, not cutting depth. Convert editable text to strokes first. Cutter spring travel and approach are checked by the core.',
        'drag_knife' =>
          'Verify blade offset, penetration, holder clearance and alignment on the actual setup. Setup verified is your physical confirmation. Feeds are ${m.units}/min. Blank cut/plunge feeds use the loaded tool; blank corner/lead feeds preserve legacy behavior. Outside loops require at least two blade offsets of radius. Edge-aware fallback uses a shallow swivel near stock edges; verify corner separation on scrap. Preview loops, swivel arcs and approach before Apply.',
        'thread_mill' =>
          'Pilot circles must match the chosen UN Class 2B standard. Verify the physical 60° UN cutter/TPI and measured pilot depth. Cutter verified is your physical confirmation.',
        _ =>
          'Use a smaller cutter than the source. Source targets and depth are inherited; source changes require a fresh review.\n${sources.map((s) => '${s['id']} = ${s['name']}').join('\n')}',
      },
      onSubmit: (p) {
        if (advancedKnife) {
          for (final e in {
            'cut_feed': 'feed_rate_override',
            'plunge_feed': 'plunge_rate_override',
            'corner_feed': 'corner_feed_ipm',
            'lead_in_feed': 'lead_in_feed_ipm',
          }.entries) {
            final raw = p.remove(e.key);
            p[e.value] = raw == null
                ? null
                : number(raw) / (e.value.endsWith('ipm') ? f : 60);
          }
        }
        return prepareOperation('special_save', p);
      },
    );
  }

  void releaseAudit([String? source]) {
    final sources = ops
        .where(
          (o) =>
              o['kind'] == 'router_offset' &&
              o['enabled'] != false &&
              ((o['profile_retention'] as Map?)?.isEmpty ?? true),
        )
        .toList();
    if (sources.isEmpty) {
      refreshSpecial(
        () => error = 'Create an enabled outside tabbed profile first.',
      );
      return;
    }
    form(
      'Audit final tab release',
      'management_prepare',
      {
        'id': source ?? sources.first['id'],
        'plate_number': m.state['activePlate'] ?? 1,
      },
      {'plate_number'},
      integers: {'plate_number'},
      choices: {
        'id': [for (final s in sources) s['id'].toString()],
      },
      note:
          'Review physical bodies, prerequisite machining and loose waste before qualifying release.\n${sources.map((s) => '${s['id']} = ${s['name']}').join('\n')}',
      onSubmit: (p) async {
        await request('management_prepare', {
          'action': 'release_audit',
          'params': p,
        });
        return true;
      },
    );
  }

  Widget releaseDetails(Json d) {
    final audit = d['audit'] as Json;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Physical bodies: ${(d['parts'] as List).join(', ')}'),
        if (d['part_order'] != null)
          Text(
            'Requested body order: ${(d['part_order'] as List).join(' → ')}',
          ),
        Text('Release events: ${jsonEncode(audit['events'])}'),
        if (d['order_locked'] == true)
          const Text('Existing locked/manual contour order is preserved.'),
        if (result['action'] == 'release_audit' && d['part_order'] != null)
          TextButton.icon(
            onPressed: d['order_locked'] == true
                ? null
                : () => pickReleaseOrder(d),
            icon: const Icon(Icons.touch_app),
            label: const Text('Pick release order in drawing…'),
          ),
        Text('Cleanup spans: ${audit['cleanup_spans']}'),
        for (final hazard in audit['hazards'] as List) Text('Blocked: $hazard'),
        for (final warning in audit['warnings'] as List)
          Text('Loose waste: ${jsonEncode(warning)}'),
        if (result['action'] == 'release_audit')
          FilledButton.tonal(
            onPressed: (audit['hazards'] as List).isEmpty
                ? () => qualifyRelease(d)
                : null,
            child: const Text('Qualify final release…'),
          ),
      ],
    );
  }

  Future<void> pickReleaseOrder(Json d) async {
    final revision = m.state['revision'];
    final picks = await showDialog<Json>(
      context: context,
      builder: (_) => ManufacturingPicker(
        model: m,
        info: {
          'kind': 'release',
          'shape': <String, dynamic>{},
          'fingerprint': d['fingerprint'],
          'eligible_ids': d['parts'],
          'operation': {
            'id': d['source_id'],
            'name': 'Plate ${d['plate_number']}',
            'part_order': d['part_order'],
          },
        },
      ),
    );
    if (!mounted || picks == null) return;
    if (revision != m.state['revision']) {
      refreshSpecial(
        () => error = 'The drawing changed. Audit final release again.',
      );
      return;
    }
    await request('management_prepare', {
      'action': 'release_audit',
      'params': {
        'id': d['source_id'],
        'plate_number': d['plate_number'],
        'fingerprint': d['fingerprint'],
        'part_order': picks['ids'],
      },
    });
  }

  void qualifyRelease(Json d) {
    form(
      'Confirm physical restraint',
      'management_prepare',
      {'evidence_note': '', 'all_bodies_fixed': false, 'waste_reviewed': false},
      {},
      booleans: {'all_bodies_fixed', 'waste_reviewed'},
      note:
          'Confirm independent restraint for EVERY listed physical body: ${(d['parts'] as List).join(', ')}. Each must remain in its original XY footprint, at or below stock top, throughout cutting and later travel. Unknown vacuum hold-down does not qualify. Describe the verified restraint method. Loose waste review does not certify scraps remain stationary.',
      onSubmit: (p) async {
        if (p['all_bodies_fixed'] != true ||
            p['evidence_note'].toString().trim().isEmpty) {
          throw ArgumentError(
            'Confirm all bodies and describe the verified restraint method.',
          );
        }
        if ((d['audit']['warnings'] as List).isNotEmpty &&
            p['waste_reviewed'] != true) {
          throw ArgumentError('Review the listed loose-waste warnings.');
        }
        await request('management_prepare', {
          'action': 'final_release',
          'params': {
            'id': d['source_id'],
            'plate_number': d['plate_number'],
            'fingerprint': d['fingerprint'],
            'confirmed_parts': d['parts'],
            if (d['part_order'] != null) 'part_order': d['part_order'],
            if (d['part_order'] != null) 'audit_key': d['warning_key'],
            'evidence_note': p['evidence_note'],
            'waste_warning_review': p['waste_reviewed'] == true
                ? d['warning_key']
                : '',
          },
        });
        return true;
      },
    );
  }
}
