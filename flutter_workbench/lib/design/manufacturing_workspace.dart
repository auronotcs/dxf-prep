import 'operation_recipe_dialog.dart';
import 'guided_readiness_dialog.dart';
import 'stock_material.dart';
import 'dart:async';
import 'dart:convert';
import 'package:flutter/services.dart';
import 'manufacturing_controls.dart';
import 'manufacturing_picker.dart';
import 'keepout_painter.dart';
import 'manufacturing_tools.dart';
import 'open_path_plates_dialog.dart';
import 'closed_overruns_dialog.dart';
import 'manufacturing_ribbon.dart';
import 'knife_clearance_dialog.dart';
import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'controller.dart';
import 'manufacturing_drawing.dart';
import 'canvas.dart' show DesignViewport;
part 'manufacturing_special.dart';
part 'manufacturing_knife.dart';
part 'manufacturing_centers.dart';
part 'manufacturing_keepouts.dart';
part 'manufacturing_linked.dart';

/// A document-owned workspace. The core owns geometry, motion, and output.
class ManufacturingWorkspace extends StatefulWidget {
  const ManufacturingWorkspace({
    super.key,
    required this.model,
    required this.onSave,
    this.initialInspectorOpen = false,
    this.view,
    this.shellBuilder,
    this.documentBarBuilder,
    this.inspectorOpen,
    this.onInspectorChanged,
    this.panelColor,
  });
  final bool initialInspectorOpen;
  final Color? panelColor;
  final DesignViewport? view;
  final Widget Function(Map<String, VoidCallback?> actions, Widget canvas)?
  shellBuilder;
  final Widget Function(Widget viewSwitch)? documentBarBuilder;
  final bool? inspectorOpen;
  final ValueChanged<bool>? onInspectorChanged;
  final DesignController model;
  final Future<bool> Function() onSave;
  @override
  State<ManufacturingWorkspace> createState() => _ManufacturingWorkspaceState();
}

class _ManufacturingWorkspaceState extends State<ManufacturingWorkspace> {
  DesignController get m => widget.model;
  Json result = {};
  Json? focusedIssue;
  int? previewPlate;
  final outputPlates = <int>{};
  Timer? timer;
  bool requesting = false, reviewed = false;
  bool overtravelReviewed = false;
  bool allowNegativeXY = false;
  bool showDrawing = true;
  late bool _inspectorOpen;
  bool get inspectorOpen => widget.inspectorOpen ?? _inspectorOpen;
  set inspectorOpen(bool value) {
    _inspectorOpen = value;
    widget.onInspectorChanged?.call(value);
  }

  bool releaseReviewed = false, knifeReviewed = false, showBaseline = false;
  bool get releaseAllowed =>
      (result['requiresReleaseReview'] != true || releaseReviewed) &&
      (result['requiresKnifeReview'] != true || knifeReviewed);
  String? error;
  dynamic revision;
  final packageName = TextEditingController(text: 'profile-trial');
  final inspectorScroll = ScrollController();
  Json get data => (m.state['manufacturing'] as Json?) ?? {};
  bool get editable => data['editable'] == true;
  bool get available => m.ready && !m.busy && !requesting;
  void refreshSpecial(VoidCallback update) => setState(update);
  @override
  void initState() {
    super.initState();
    _inspectorOpen = widget.initialInspectorOpen;
    revision = m.state['revision'];
    outputPlates.addAll(m.plates.map((p) => p['number'] as int));
    m.addListener(changed);
  }

  void changed() {
    if (revision != m.state['revision']) {
      revision = m.state['revision'];
      timer?.cancel();
      result = {};
      reviewed = false;
      overtravelReviewed = false;
      allowNegativeXY = false;
      releaseReviewed = false;
      knifeReviewed = false;
      focusedIssue = null;
      showDrawing = true;
      final existing = m.plates.map((p) => p['number'] as int).toSet();
      outputPlates.removeWhere((n) => !existing.contains(n));
      if (existing.length == 1) {
        outputPlates.add(existing.single);
      }
    }
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    timer?.cancel();
    m.removeListener(changed);
    if (!m.stopped && m.ready) {
      unawaited(
        m
            .request('manufacturing.cancel')
            .catchError((Object _) => <String, dynamic>{}),
      );
    }
    inspectorScroll.dispose();
    packageName.dispose();
    super.dispose();
  }

  Future<void> request(String action, [Json params = const {}]) async {
    if (requesting) return;
    setState(() {
      requesting = true;
      error = null;
    });
    final startRevision = m.state['revision'];
    try {
      final response = await m.request('manufacturing.$action', params);
      if (response['ok'] != true) {
        throw StateError(response['error'].toString());
      }
      if (!mounted || m.state['revision'] != startRevision) return;
      result = response['manufacturing'] as Json;
      inspectorOpen = true;
      if ([
        'ready',
        'operation_ready',
        'group_ready',
        'optimization_ready',
        'management_ready',
        'blocked',
      ].contains(result['status'])) {
        showDrawing = false;
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted && inspectorScroll.hasClients) {
            inspectorScroll.jumpTo(0);
          }
        });
      }
      if (action != 'poll') {
        reviewed = false;
        overtravelReviewed = false;
        allowNegativeXY = false;
        releaseReviewed = false;
        knifeReviewed = false;
        showBaseline = false;
        previewPlate = null;
        focusedIssue = null;
      }
      timer?.cancel();
      if (result['status'] == 'running') {
        timer = Timer(const Duration(milliseconds: 350), () => request('poll'));
      }
    } catch (e) {
      if (mounted) {
        error = e.toString();
        timer?.cancel();
      }
    } finally {
      if (mounted) setState(() => requesting = false);
    }
  }

  Future<void> edit(String command, Json params) async {
    if (!await m.run('manufacturing.$command', params) && mounted) {
      setState(() => error = m.message);
    }
  }

  Future<void> form(
    String title,
    String command,
    Json values,
    Set<String> numeric, {
    Set<String> integers = const {},
    Set<String> booleans = const {},
    Set<String> numberLists = const {},
    Set<String> optional = const {},
    String? note,
    Set<String> fixed = const {},
    bool creatingCutter = false,
    Json extra = const {},
    List<Json>? contours,
    Set<String>? selected,
    Set<String>? islands,
    Map<String, List<String>> choices = const {},
    Future<bool> Function(Json)? onSubmit,
  }) async {
    final controllers = {
      for (final e in values.entries)
        e.key: TextEditingController(
          text: e.value is List
              ? (e.value as List).join(', ')
              : e.value?.toString() ?? '',
        ),
    };
    final ids = {...?selected};
    // A new operation inherits only geometry compatible with its editor.
    // Preserve existing operation IDs so hidden/locked targets cannot be silently dropped.
    if (extra['id'] == null && contours != null) {
      final eligible = contours.map((e) => e['id']).toSet();
      ids.removeWhere((id) => !eligible.contains(id));
    }
    final islandIds = {...?islands};
    String? formError;
    bool applying = false;
    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, update) => AlertDialog(
          title: Text(title),
          content: SizedBox(
            width: 480,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (note != null)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: Text(note),
                    ),
                  for (final e in controllers.entries)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 10),
                      child: booleans.contains(e.key)
                          ? CheckboxListTile(
                              title: Text(label(e.key)),
                              value: e.value.text == 'true',
                              onChanged: applying
                                  ? null
                                  : (v) => update(
                                      () =>
                                          e.value.text = (v == true).toString(),
                                    ),
                            )
                          : choices.containsKey(e.key)
                          ? DropdownButtonFormField<String>(
                              initialValue:
                                  choices[e.key]!.contains(e.value.text)
                                  ? e.value.text
                                  : choices[e.key]!.first,
                              decoration: InputDecoration(
                                labelText: label(e.key),
                                border: const OutlineInputBorder(),
                              ),
                              items: [
                                for (final option in choices[e.key]!)
                                  DropdownMenuItem(
                                    value: option,
                                    child: Text(
                                      (e.key == 'source_operation_id' ||
                                              e.key == 'id')
                                          ? ((data['operations'] as List? ?? [])
                                                    .where(
                                                      (o) => o['id'] == option,
                                                    )
                                                    .firstOrNull?['name']
                                                    ?.toString() ??
                                                option)
                                          : option,
                                    ),
                                  ),
                              ],
                              onChanged: applying
                                  ? null
                                  : (v) {
                                      if (v != null) e.value.text = v;
                                    },
                            )
                          : TextField(
                              controller: e.value,
                              enabled: !applying && !fixed.contains(e.key),
                              decoration: InputDecoration(
                                labelText: label(e.key),
                                border: const OutlineInputBorder(),
                              ),
                            ),
                    ),
                  if (contours != null) ...[
                    const Text('Contours to cut (visible, unlocked)'),
                    for (final e in contours)
                      CheckboxListTile(
                        dense: true,
                        title: Text(
                          '${e['kind']} - ${e['layer']} - ${shortId(e['id'].toString())}',
                        ),
                        value: ids.contains(e['id']),
                        onChanged: applying
                            ? null
                            : (v) => update(() {
                                if (v == true) {
                                  ids.add(e['id'] as String);
                                  islandIds.remove(e['id']);
                                } else {
                                  ids.remove(e['id']);
                                }
                              }),
                      ),
                  ],
                  if (islands != null && contours != null) ...[
                    const Text(
                      'Protected islands (exclude these from the cut)',
                    ),
                    for (final e in contours)
                      CheckboxListTile(
                        dense: true,
                        title: Text(
                          '${e['kind']} - ${shortId(e['id'].toString())}',
                        ),
                        value: islandIds.contains(e['id']),
                        onChanged: applying
                            ? null
                            : (v) => update(() {
                                if (v == true) {
                                  islandIds.add(e['id'] as String);
                                  ids.remove(e['id']);
                                } else {
                                  islandIds.remove(e['id']);
                                }
                              }),
                      ),
                  ],
                  if (formError != null)
                    Text(
                      formError!,
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.error,
                      ),
                    ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: applying ? null : () => Navigator.pop(dialogContext),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: applying
                  ? null
                  : () async {
                      final params = <String, dynamic>{...extra};
                      try {
                        for (final e in controllers.entries) {
                          final text = e.value.text.trim();
                          if (numberLists.contains(e.key)) {
                            final parts = text.isEmpty
                                ? <String>[]
                                : text.split(RegExp(r'[,;\s]+'));
                            final values = parts.map(double.tryParse).toList();
                            final manual = e.key == 'manual_tab_positions';
                            if (values.any(
                              (v) =>
                                  v == null ||
                                  !v.isFinite ||
                                  (manual ? v < 0 || v >= 100 : v <= 0),
                            )) {
                              throw FormatException(
                                manual
                                    ? 'Enter tab centers from 0 up to, but not including, 100 percent.'
                                    : 'Enter positive pass increments separated by commas.',
                              );
                            }
                            params[e.key] = values
                                .cast<double>()
                                .map((v) => manual ? v / 100 : v)
                                .toList();
                          } else if (booleans.contains(e.key)) {
                            params[e.key] = text == 'true';
                          } else if (optional.contains(e.key) && text.isEmpty) {
                            params[e.key] = null;
                          } else if (numeric.contains(e.key)) {
                            final value = integers.contains(e.key)
                                ? int.tryParse(text)
                                : double.tryParse(text);
                            if (value == null || !value.isFinite) {
                              throw FormatException(
                                'Enter a valid ${label(e.key)}.',
                              );
                            }
                            params[e.key] = value;
                          } else {
                            params[e.key] = text;
                          }
                        }
                        if (creatingCutter &&
                            (data['tools'] as List? ?? []).any(
                              (t) => t['station'] == params['station'],
                            )) {
                          throw const FormatException(
                            'That cutter station is occupied. Edit its existing cutter instead.',
                          );
                        }
                        if (contours != null) {
                          params['entity_ids'] = ids.toList();
                        }
                        if (islands != null) {
                          params['island_entity_ids'] = islandIds.toList();
                        }
                        update(() => applying = true);
                        final ok = onSubmit != null
                            ? await onSubmit(params)
                            : await m.run('manufacturing.$command', params);
                        if (!dialogContext.mounted) return;
                        if (ok) {
                          Navigator.pop(dialogContext);
                        } else {
                          update(() {
                            applying = false;
                            formError = m.message;
                          });
                        }
                      } catch (e) {
                        update(() {
                          applying = false;
                          formError = e.toString();
                        });
                      }
                    },
              child: const Text('Apply'),
            ),
          ],
        ),
      ),
    );
    // Dispose after the route's closing animation releases its text fields.
    await Future<void>.delayed(const Duration(milliseconds: 250));
    for (final c in controllers.values) {
      c.dispose();
    }
  }

  String blockDetails(Json details) {
    final parts = <String>[];
    final role = switch (details['role']) {
      'sliver_cleanup' => 'Short-loop cleanup',
      'common_line_pair' => 'Shared rectangle edge',
      'tab_cleanup' => 'Tab removal / part release',
      'region_fill' => 'Concentric clearing',
      _ => null,
    };
    if (role != null) parts.add(role);
    if (details['entity_id'] != null) {
      parts.add('Contour ${shortId(details['entity_id'].toString())}');
    }
    if (details['entity_ids'] is List) {
      parts.add(
        'Contours ${(details['entity_ids'] as List).map((id) => shortId(id.toString())).join(', ')}',
      );
    }
    for (final entry in <String, String>{
      'depth': 'Depth (${m.units})',
      'finished_tab_width': 'Retained tab width (${m.units})',
      'raised_tab_span': 'Raised cutter span (${m.units})',
      'offset_distance': 'Cutter offset (${m.units})',
      'side': 'Cut side',
      'entry_mode': 'Requested entry',
      'entry_mode_actual': 'Actual entry',
      'entry_effective_start': 'Accepted entry X/Y (${m.units})',
      'entry_original_start': 'Original entry X/Y (${m.units})',
      'entry_rejection_reason': 'Why entry moved',
      'entry_adjustment_kind': 'Entry adjustment',
      'entry_fallback_reason_code': 'Fallback reason',
      'loop_count': 'Loops',
      'linked_count': 'Proven links',
      'plunge_count': 'Plunges',
      'common_line_length': 'Shared edge (${m.units})',
      'common_line_lifts_saved': 'Lifts saved',
      'curve_feed_adjusted_motions': 'Feed-adjusted moves',
      'curve_feed_minimum': 'Lowest curve feed (${m.units}/s)',
    }.entries) {
      if (details[entry.key] != null) {
        parts.add('${entry.value}: ${details[entry.key]}');
      }
    }
    return parts.join(' · ');
  }

  String shortId(String id) {
    final part = id.split('_').last;
    return part.substring(0, math.min(6, part.length));
  }

  String label(String key) => key.startsWith('primary_')
      ? 'Primary ${label(key.substring(8))}'
      : switch (key) {
          'feed_rate' => 'Cut feed (${m.units}/second)',
          'plunge_rate' => 'Plunge feed (${m.units}/second)',
          'spindle_rpm' => 'Spindle RPM',
          'side' => 'Cut side',
          'cut_direction' => 'Direction (climb or conventional)',
          'station' || 'tool_station' => 'Cutter station (1-12)',
          'name' => 'Name',
          'evidence_note' => 'Evidence note',
          'all_bodies_fixed' => 'All bodies fixed',
          'waste_reviewed' => 'Waste reviewed',
          'setup_verified' => 'Knife setup physically verified',
          'cut_feed' => 'Cut feed (${m.units}/min, blank = cutter)',
          'plunge_feed' => 'Plunge feed (${m.units}/min, blank = cutter)',
          'corner_feed' => 'Corner feed (${m.units}/min, blank = legacy)',
          'lead_in_feed' => 'Lead-in feed (${m.units}/min, blank = legacy)',
          'outside_corner_loops' => 'Outside corner loops',
          'edge_aware_corners' => 'Edge-aware corner fallback',
          'loop_radius' => 'Loop radius (${m.units})',
          'loop_feed_percent' => 'Legacy loop feed (%)',
          'cutter_verified' => 'Thread cutter / TPI / UN form verified',
          'material' => 'Material',
          'material_detail' => 'Material detail',
          'machine' => 'Machine setup name',
          'contour_order' => 'Contour order',
          'curve_feed_mode' => 'Curve feed',
          'curve_feed_min_percent' => 'Minimum curve feed (%)',
          'curve_feed_transition_distance' =>
            'Curve feed transition (${m.units})',
          'common_line_mode' => 'Shared rectangle edge',
          'common_line_tolerance' => 'Shared-edge match tolerance (${m.units})',
          'sliver_cleanup' => 'Clean short residual loops once at final depth',
          'sliver_max_path_length' => 'Maximum short-loop length (${m.units})',
          'sliver_link_distance' =>
            'Short-loop link distance (${m.units}, 0 = retract)',
          'peck_definition' => 'Peck definition',
          'peck_count' => 'Equal peck count',
          'surface_feed_override' =>
            'Surface approach feed (${m.units}/s, blank = cutter)',
          'retract_feed_override' =>
            'Controlled retract feed (${m.units}/s, blank = cutter)',
          'plunge_rate_override' =>
            'Drilling feed (${m.units}/s, blank = cutter)',
          'spindle_rpm_override' => 'Drilling RPM (blank = cutter)',
          'controlled_retract' => 'Feed during chip-break retracts',
          'return_to_surface' => 'Feed to surface before final rapid',
          'top_dwell_seconds' => 'Dwell after retract (seconds)',
          'pass_depths' => 'Pass increments (${m.units}, comma separated)',
          'pass_mode' => 'Roughing pass schedule',
          'pass_count' => 'Equal roughing pass count',
          'corner_tags' => 'Cover cleanout corners',
          'bottom_finish_pass' => 'Add floor finishing pass',
          'bottom_finish_depth' => 'Floor stock (${m.units})',
          'bottom_finish_feed_override' =>
            'Floor finishing feed (${m.units}/s, blank = cutter)',
          'keep_tool_down' => 'Keep tool down between proven clear loops',
          'stay_down_max_distance' => 'Maximum link distance (${m.units})',
          'stay_down_max_engagement' => 'Maximum link engagement (${m.units})',
          'stay_down_feed_percent' => 'Link feed (% of cut feed)',
          'entry_mode' => 'Entry mode',
          'entry_side' => 'Helix waste side',
          'helix_fallback' => 'If helix will not fit',
          'helix_diameter' => 'Preferred helix orbit diameter (${m.units})',
          'helix_min_diameter' => 'Minimum helix orbit diameter (${m.units})',
          'helix_pitch' => 'Maximum helix pitch (${m.units}/revolution)',
          'helix_ramp_angle' => 'Maximum helix angle (degrees; 0 = pitch only)',
          'helix_clearance' => 'Additional helix clearance (${m.units})',
          'helix_finish_revolutions' =>
            'Final-depth helix revolutions (whole number)',
          'finishing_pass' => 'Add finishing / spring pass',
          'finish_feed_override' || 'helix_feed_override' =>
            '${key.startsWith('finish') ? 'Finishing' : 'Helix'} feed (${m.units}/second; blank inherits)',
          'finish_spindle_rpm_override' => 'Finishing RPM (blank inherits)',
          'strategy' => 'Cleanout strategy',
          'speed_fallback' => 'If full-depth proof fails',
          'speed_center_feed_percent' => 'Center feed (% of cut feed)',
          'retain_tabs' => 'Leave this many tabs per component',
          'tab_count' => 'Requested tab count',
          'manual_tab_positions' => 'Manual tab centers (%, comma separated)',
          'start_index' => 'Preferred start vertex / circle quadrant (0-based)',
          'remove_tabs_after' =>
            'Remove eligible tabs after cutting (releases parts)',
          'tab_cleanup_order' => 'Tab removal timing',
          'tab_cleanup_min_perimeter' =>
            'Minimum perimeter for tab removal (${m.units})',
          'enabled' => 'Enable cycle optimization',
          'depth_linking' => 'Link eligible profile depth passes',
          'profile_ordering' => 'Optimize profile travel order',
          'profile_travel_strategy' => 'Profile travel strategy',
          'profile_start_optimization' =>
            'Optimize eligible profile start points',
          'drill_ordering' => 'Optimize drilling order',
          'motion_compaction' => 'Merge redundant line motions',
          'line_merge_tolerance' => 'Line merge tolerance (${m.units})',
          'rapid_xy_ipm' => 'Estimated XY rapid (inches/minute)',
          'rapid_z_ipm' => 'Estimated Z rapid (inches/minute)',
          'tool_change_seconds' => 'Tool-change allowance (seconds)',
          'spindle_start_seconds' => 'Spindle-start allowance (seconds)',
          'tab_mode' => 'Automatic tab placement',
          'tab_width' => 'Finished tab width (${m.units})',
          'tab_height' => 'Retained tab height (${m.units})',
          'tab_spacing' => 'Maximum tab center spacing (${m.units})',
          'corner_tab_offset' => 'Tab offset from corners (${m.units})',
          'female_tab_min_perimeter' =>
            'Minimum inside perimeter for tabs (${m.units})',
          'onion_skin' => 'Roughing onion skin (${m.units})',
          'breakthrough' => 'Through-stock breakthrough (${m.units})',
          'cut_intent' => 'Depth intent',
          'kind' => 'Cutter kind',
          'peck_mode' => 'Peck mode',
          'dwell_seconds' => 'Bottom dwell (seconds)',
          _ => '${key.replaceAll('_', ' ')} (${m.units})',
        };
  double get unitFactor => switch (m.units) {
    'mm' => 25.4,
    'cm' => 2.54,
    'm' => .0254,
    'ft' => 1 / 12,
    'yd' => 1 / 36,
    _ => 1,
  };
  void stock() {
    final setup = (m.state['setup'] as Json?) ?? {};
    final f = unitFactor;
    form(
      'Stock / job setup',
      'setup',
      {
        'material': setup['material'] ?? 'Anything',
        'machine': setup['machine'] ?? 'Shop Router / EnRoute',
        'through_cut_breakthrough':
            setup['through_cut_breakthrough'] ?? .015 * f,
        'material_detail': setup['material_detail'] ?? '',
        'material_thickness': setup['material_thickness'] ?? .5 * f,
        'safe_z': setup['safe_z'] ?? .25 * f,
        if (m.plates.length <= 1)
          'width': m.plates.isEmpty ? 96 * f : m.plates.single['width'],
        if (m.plates.length <= 1)
          'height': m.plates.isEmpty ? 48 * f : m.plates.single['height'],
      },
      {
        'material_thickness',
        'safe_z',
        'width',
        'height',
        'through_cut_breakthrough',
      },
    );
  }

  Future<void> guidedReadiness() async {
    if (!available || m.state['guidedReadiness'] != true) return;
    final action = await showDialog<Json>(
      context: context,
      builder: (_) => GuidedReadinessDialog(model: m),
    );
    if (!mounted || action == null) return;
    switch (action['action']) {
      case 'stock':
        stock();
      case 'verify':
        await StockMaterialControls(model: m).verify(context);
      case 'turret':
        await toolsDialog();
      case 'library':
        await toolsDialog(library: true);
      case 'operation':
        final op = (data['operations'] as List? ?? [])
            .cast<Json>()
            .where((r) => r['id'] == action['operation_id'])
            .firstOrNull;
        if (op != null && editable) operationEditor(op);
      case 'attach':
        final draft = await showDialog<Json>(
          context: context,
          builder: (_) => OperationRecipeDialog(
            model: m,
            operationId: action['operation_id'] as String,
          ),
        );
        if (mounted && draft != null) {
          await request('attachment_prepare', draft);
        }
      case 'locate':
        final box = bounds(action['bounds']);
        if (box != null) {
          widget.view?.fit(box);
          setState(() => showDrawing = true);
        }
    }
  }

  Future<void> toolsDialog({bool library = false}) async {
    final draft = await showDialog<Json>(
      context: context,
      builder: (_) => ManufacturingToolsDialog(model: m, library: library),
    );
    if (draft != null && mounted) {
      await request('management_prepare', {
        'action': 'turret',
        'params': draft,
      });
    }
  }

  void cutter([Json? tool]) {
    final f = unitFactor;
    final stations = (data['tools'] as List? ?? [])
        .map((t) => t['station'])
        .toSet();
    final next =
        List.generate(
          12,
          (i) => i + 1,
        ).where((i) => !stations.contains(i)).firstOrNull ??
        1;
    final defaults = <String, dynamic>{
      'station': next,
      'name': 'End mill',
      'kind': 'end_mill',
      'diameter': .25 * f,
      'spindle_rpm': 18000,
      'feed_rate': 2 * f,
      'plunge_rate': .5 * f,
      'max_stepdown': .125 * f,
      'cutting_edge_length': .75 * f,
    };
    form(
      tool == null ? 'Add cutter' : 'Edit cutter',
      'tool_save',
      {for (final e in defaults.entries) e.key: tool?[e.key] ?? e.value},
      defaults.keys.where((k) => !{'name', 'kind'}.contains(k)).toSet(),
      choices: const {
        'kind': ['end_mill', 'drill'],
      },
      integers: {'station', 'spindle_rpm'},
      fixed: tool == null ? {} : {'station'},
      creatingCutter: tool == null,
    );
  }

  void profile([Json? operation]) {
    final setup = (m.state['setup'] as Json?) ?? {};
    final tools = (data['tools'] as List? ?? []).cast<Json>();
    final f = unitFactor;
    final defaults = <String, dynamic>{
      'name': 'Profile',
      'tool_station': tools.firstOrNull?['station'] ?? 1,
      'target_depth': setup['material_thickness'] ?? .5 * f,
      'safe_z': setup['safe_z'] ?? .25 * f,
      'stepdown': tools.firstOrNull?['max_stepdown'] ?? .125 * f,
      'side': 'outside',
      'cut_direction': 'climb',
    };
    final contours = m.entities
        .where(
          (e) =>
              ['circle', 'rectangle', 'polyline'].contains(e['kind']) &&
              (e['kind'] != 'polyline' || e['closed'] == true) &&
              m.visible(e) &&
              !m.locked(e),
        )
        .toList();
    form(
      operation == null ? 'Add basic profile' : 'Edit basic profile',
      'profile_save',
      {for (final e in defaults.entries) e.key: operation?[e.key] ?? e.value},
      {'tool_station', 'target_depth', 'safe_z', 'stepdown'},
      integers: {'tool_station'},
      choices: const {
        'side': ['inside', 'outside'],
        'cut_direction': ['climb', 'conventional'],
      },
      extra: {if (operation != null) 'id': operation['id']},
      contours: contours,
      selected: operation != null
          ? Set<String>.from(operation['entity_ids'])
          : m.selection,
    );
  }

  List<Json> get closedContours => m.entities
      .where(
        (e) =>
            ['circle', 'rectangle', 'polyline'].contains(e['kind']) &&
            (e['kind'] != 'polyline' || e['closed'] == true) &&
            m.visible(e) &&
            !m.locked(e),
      )
      .toList();

  void operationEditor(Json op) {
    if (op['kind'] == 'tab_release') {
      releaseAudit(op['source_operation_id'].toString());
      return;
    }
    if (specialNames.containsKey(op['kind'])) {
      specialOperation(op['kind'].toString(), op);
      return;
    }
    if ((op['profile_retention'] as Map?)?.isNotEmpty == true) {
      group(op);
      return;
    }
    switch (op['kind']) {
      case 'drill_circles':
        advancedDrill(op);
      case 'island_fill':
        advancedFill(op);
      default:
        if (isCutLine(op)) {
          cutLine(op);
          return;
        }
        if (number(op['start_index']) != 0 ||
            op['remove_tabs_after'] == true ||
            (op['manual_tab_positions'] as List? ?? []).isNotEmpty ||
            (op['tab_cleanup_order'] ?? 'after_each_part') !=
                'after_each_part' ||
            number(op['tab_cleanup_min_perimeter']) != 0 ||
            op['side'] == 'on' ||
            (op['cut_intent'] ?? 'custom') != 'custom' ||
            number(op['breakthrough']) != 0 ||
            number(op['onion_skin']) != 0 ||
            number(op['tab_count']) != 0 ||
            (op['tab_mode'] ?? 'even') != 'even' ||
            number(op['tab_width']) != 0 ||
            number(op['tab_height']) != 0 ||
            number(op['corner_tab_offset'] ?? 1) != 1 ||
            number(op['tab_spacing'] ?? 8) != 8 ||
            number(op['female_tab_min_perimeter'] ?? 4) != 4) {
          advancedProfile(op);
          return;
        }
        if (op['entry_mode'] != null &&
            (op['entry_mode'] != 'plunge' ||
                (op['pass_depths'] as List? ?? []).isNotEmpty ||
                (op['contour_order'] ?? 'inside_first') != 'inside_first' ||
                (op['curve_feed_mode'] ?? 'off') != 'off' ||
                number(op['curve_feed_min_percent'] ?? 25) != 25 ||
                number(op['curve_feed_transition_distance'] ?? .5) != .5 ||
                (op['common_line_mode'] ?? 'off') != 'off' ||
                number(op['common_line_tolerance'] ?? .001) != .001 ||
                op['finishing_pass'] == true ||
                number(op['allowance']) != 0 ||
                number(op['lead_in']) != 0 ||
                number(op['lead_out']) != 0 ||
                number(op['finish_axial_stock']) != 0 ||
                number(op['helix_diameter']) != 0 ||
                number(op['helix_pitch']) != 0 ||
                number(op['helix_min_diameter']) != 0 ||
                number(op['helix_ramp_angle']) != 0 ||
                number(op['helix_clearance']) != 0 ||
                number(op['helix_finish_revolutions']) != 0 ||
                op['helix_feed_override'] != null ||
                (op['entry_side'] ?? 'automatic_waste') != 'automatic_waste' ||
                (op['helix_fallback'] ?? 'waste_plunge') != 'waste_plunge' ||
                op['finish_feed_override'] != null ||
                op['finish_spindle_rpm_override'] != null)) {
          advancedProfile(op);
        } else {
          profile(op);
        }
    }
  }

  void machining(String kind, [Json? operation]) {
    final drill = kind == 'drill';
    final tool = (data['tools'] as List? ?? [])
        .cast<Json>()
        .where((t) => drill || t['kind'] == 'end_mill')
        .firstOrNull;
    final defaults = <String, dynamic>{
      'name': drill ? 'Drill circles' : 'Concentric cleanout',
      'tool_station': tool?['station'] ?? 1,
      'target_depth':
          m.state['setup']?['material_thickness'] ?? .125 * unitFactor,
      'safe_z': m.state['setup']?['safe_z'] ?? .25 * unitFactor,
      if (drill) ...{
        'peck_depth': .05 * unitFactor,
        'peck_mode': 'full_clear',
        'retract_height': .1 * unitFactor,
        'reentry_clearance': .02 * unitFactor,
        'dwell_seconds': 0,
      } else ...{
        'stepdown': tool?['max_stepdown'] ?? .125 * unitFactor,
        'stepover': number(tool?['diameter'] ?? .25 * unitFactor) * .4,
        'cut_direction': 'climb',
      },
    };
    form(
      operation == null
          ? (drill ? 'Add drilling' : 'Add concentric cleanout')
          : (drill ? 'Edit drilling' : 'Edit concentric cleanout'),
      '${kind}_save',
      {for (final e in defaults.entries) e.key: operation?[e.key] ?? e.value},
      defaults.keys
          .where((k) => !{'name', 'peck_mode', 'cut_direction'}.contains(k))
          .toSet(),
      integers: {'tool_station'},
      choices: drill
          ? const {
              'peck_mode': ['none', 'chip_break', 'full_clear'],
            }
          : const {
              'cut_direction': ['climb', 'conventional'],
            },
      extra: {if (operation != null) 'id': operation['id']},
      contours: closedContours
          .where((e) => !drill || e['kind'] == 'circle')
          .toList(),
      selected: operation != null
          ? Set<String>.from(operation['entity_ids'])
          : m.selection,
      islands: drill
          ? null
          : Set<String>.from(operation?['island_entity_ids'] ?? []),
    );
  }

  void advancedDrill([Json? op]) {
    final tool = (data['tools'] as List? ?? [])
        .cast<Json>()
        .where((t) => ['end_mill', 'drill'].contains(t['kind']))
        .firstOrNull;
    final defaults = <String, dynamic>{
      'name': op?['name'] ?? 'Drill circles',
      'tool_station': op?['tool_station'] ?? tool?['station'] ?? 1,
      'target_depth':
          op?['target_depth'] ??
          m.state['setup']?['material_thickness'] ??
          .125 * unitFactor,
      'safe_z':
          op?['safe_z'] ?? m.state['setup']?['safe_z'] ?? .25 * unitFactor,
      'peck_mode': op?['peck_mode'] ?? 'full_clear',
      'peck_definition': op?['peck_definition'] ?? 'max_depth',
      'peck_depth': op == null ? .05 * unitFactor : op['peck_depth'],
      'peck_count': op?['peck_count'],
      'retract_height': op?['retract_height'] ?? .1 * unitFactor,
      'reentry_clearance': op?['reentry_clearance'] ?? .02 * unitFactor,
      'surface_feed_override': op?['surface_feed_override'],
      'plunge_rate_override': op?['plunge_rate_override'],
      'retract_feed_override': op?['retract_feed_override'],
      'spindle_rpm_override': op?['spindle_rpm_override'],
      'controlled_retract': op?['controlled_retract'] ?? false,
      'return_to_surface': op?['return_to_surface'] ?? false,
      'dwell_seconds': op?['dwell_seconds'] ?? 0,
      'top_dwell_seconds': op?['top_dwell_seconds'] ?? 0,
    };
    form(
      'Drilling cycle draft',
      'operation_prepare',
      defaults,
      defaults.keys
          .where(
            (k) => !{
              'name',
              'peck_mode',
              'peck_definition',
              'controlled_retract',
              'return_to_surface',
            }.contains(k),
          )
          .toSet(),
      integers: {'tool_station', 'peck_count', 'spindle_rpm_override'},
      booleans: {'controlled_retract', 'return_to_surface'},
      optional: {
        'peck_depth',
        'peck_count',
        'surface_feed_override',
        'plunge_rate_override',
        'retract_feed_override',
        'spindle_rpm_override',
      },
      choices: const {
        'peck_mode': ['none', 'chip_break', 'full_clear'],
        'peck_definition': ['max_depth', 'count'],
      },
      extra: {if (op != null) 'id': op['id']},
      contours: closedContours.where((e) => e['kind'] == 'circle').toList(),
      selected: op == null ? m.selection : Set<String>.from(op['entity_ids']),
      note:
          'Equal count divides final depth into equal pecks. Full clear retracts above stock; chip break lifts from the current depth. Review actual peck depths and withdrawal motion. Feeds are drawing units per second. Nonzero dwell is not qualified for the current EnRoute output dialect.',
      onSubmit: (p) => prepareOperation('drill_save', p),
    );
  }

  Future<bool> prepareOperation(String command, Json params) async {
    await request('operation_prepare', {'command': command, 'params': params});
    return error == null;
  }

  void linkedError(String message) {
    setState(() {
      inspectorOpen = true;
      error = message;
    });
  }

  Future<void> management(String action, [Json? op]) async {
    Json? params;
    if (action == 'small_centers') {
      await smallCenters(op!);
      return;
    }
    if (action.startsWith('group_')) {
      await linkedEditor(action, op!);
      return;
    }
    if (action.startsWith('pick_')) {
      await graphicalPick(action.substring(5), op!);
      return;
    }
    if (action == 'retained_cleanup') {
      await form(
        'Plan retained tab cleanup',
        'management_prepare',
        {'count': 2, 'plate': m.plates.firstOrNull?['number'] ?? 1},
        {'count', 'plate'},
        integers: {'count', 'plate'},
        note:
            'Retain at least this many original physical tabs per part. The core checks surviving stock connectivity and uses only existing cleanup spans. This does not qualify fixtures or release parts.',
        onSubmit: (p) async {
          await request('management_prepare', {
            'action': action,
            'params': {'id': op!['id'], ...p},
          });
          return error == null;
        },
      );
      return;
    } else if (action == 'keep_tabs') {
      params = {'id': op!['id']};
    } else if (action == 'profile_controls') {
      params = await profileContourControls(context, m, op!);
    } else if (action == 'sequence') {
      params = await operationSequenceControls(
        context,
        op!,
        List<Json>.from(data['operations'] as List),
      );
    } else if (action == 'associate') {
      params = {
        'id': op!['id'],
        'enabled': (op['geometry_selector'] as Map? ?? {}).isEmpty,
      };
    } else if (action == 'revision') {
      if ((data['operations'] as List).any(
            (o) => (o['profile_retention'] as Map? ?? {}).isNotEmpty,
          ) &&
          data['linkedRevisions'] != true) {
        linkedError(
          'This tab uses an earlier backend. Save and reopen when convenient to review linked DXF revisions.',
        );
        return;
      }
      try {
        final path = await const MethodChannel(
          'workbench/design',
        ).invokeMethod<String>('open');
        if (path != null) params = {'path': path};
      } catch (e) {
        if (mounted) setState(() => error = 'File dialog unavailable: $e');
      }
    } else if (action == 'import_groups') {
      params = {};
    }
    if (params != null && mounted) {
      await request('management_prepare', {'action': action, 'params': params});
    }
  }

  Future<void> graphicalPick(String kind, Json op) async {
    if (data['graphicalPicking'] != true) {
      setState(() {
        inspectorOpen = true;
        error =
            'This tab uses an earlier backend. New tabs use graphical tools; save and reopen this drawing when convenient.';
      });
      return;
    }
    final before = m.state['revision'];
    try {
      final response = await m.request('manufacturing.picking_info', {
        'id': op['id'],
        'kind': kind,
      });
      if (response['ok'] != true) {
        throw StateError(response['error'].toString());
      }
      if (!mounted || m.state['revision'] != before) return;
      final picked = await showDialog<Json>(
        context: context,
        barrierDismissible: false,
        builder: (_) => ManufacturingPicker(
          model: m,
          info: response['manufacturing'] as Json,
        ),
      );
      if (picked == null || !mounted) return;
      if (m.state['revision'] != before) {
        throw StateError('Drawing changed; reopen the graphical picker.');
      }
      await request('management_prepare', {
        'action': 'graphical',
        'params': picked,
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          inspectorOpen = true;
          error = e.toString();
        });
      }
    }
  }

  Future<void> pickingMenu() async {
    final ops = (data['operations'] as List? ?? [])
        .cast<Json>()
        .where(
          (o) =>
              (o['profile_retention'] as Map? ?? {}).isEmpty &&
              ['router_offset', 'island_fill'].contains(o['kind']),
        )
        .toList();
    final picked = await showDialog<Json>(
      context: context,
      builder: (c) => SimpleDialog(
        title: const Text('Graphical Manufacturing tools'),
        children: [
          if (ops.isEmpty)
            const Padding(
              padding: EdgeInsets.all(16),
              child: Text('Create an independent profile or cleanout first.'),
            ),
          for (final op in ops)
            for (final kind
                in op['kind'] == 'island_fill'
                    ? ['boundaries', 'islands']
                    : ['start', 'tabs', 'order'])
              SimpleDialogOption(
                onPressed: () => Navigator.pop(c, <String, dynamic>{
                  'kind': kind,
                  'operation': op,
                }),
                child: Text('${op['name']} · ${pickingNames[kind]}'),
              ),
        ],
      ),
    );
    if (picked != null && mounted) {
      await graphicalPick(
        picked['kind'] as String,
        picked['operation'] as Json,
      );
    }
  }

  void optimization() {
    final stored = data['optimization'] as Json? ?? {};
    final defaults = <String, dynamic>{
      'enabled': true,
      'depth_linking': true,
      'profile_ordering': true,
      'profile_travel_strategy': 'nearest_safe',
      'profile_start_optimization': true,
      'drill_ordering': true,
      'motion_compaction': true,
      'line_merge_tolerance': .00005 * unitFactor,
      'rapid_xy_ipm': 600,
      'rapid_z_ipm': 300,
      'tool_change_seconds': 8,
      'spindle_start_seconds': 2,
    };
    if (stored['enabled'] == true) {
      for (final key in defaults.keys.toList()) {
        if (stored.containsKey(key)) defaults[key] = stored[key];
      }
    }
    form(
      'Cycle optimization comparison',
      'optimization_prepare',
      defaults,
      {
        'line_merge_tolerance',
        'rapid_xy_ipm',
        'rapid_z_ipm',
        'tool_change_seconds',
        'spindle_start_seconds',
      },
      booleans: {
        'enabled',
        'depth_linking',
        'profile_ordering',
        'profile_start_optimization',
        'drill_ordering',
        'motion_compaction',
      },
      choices: const {
        'profile_travel_strategy': [
          'generated',
          'nearest_safe',
          'y_serpentine',
          'small_edge_first',
        ],
      },
      note:
          'Compare original motion with the requested settings before Apply. Estimates use the rapid rates and allowances below, not measured machine time. The core preserves operation order and release prerequisites. Disable start optimization to preserve preferred profile start vertices. Low retracts and arc fitting remain disabled. Output still requires generation and review for the selected plates.',
      onSubmit: (p) async {
        await request('optimization_prepare', p);
        return error == null;
      },
    );
  }

  bool isCutLine(Json op) => m.entities.any(
    (e) =>
        (op['entity_ids'] as List? ?? []).contains(e['id']) &&
        (e['kind'] == 'line' || e['kind'] == 'polyline' && e['closed'] != true),
  );

  Json profileSeed(String name, String side) {
    final tool = (data['tools'] as List? ?? [])
        .cast<Json>()
        .where((t) => t['kind'] == 'end_mill')
        .firstOrNull;
    return {
      'name': name,
      'tool_station': tool?['station'] ?? 1,
      'target_depth':
          m.state['setup']?['material_thickness'] ?? .5 * unitFactor,
      'safe_z': m.state['setup']?['safe_z'] ?? .25 * unitFactor,
      'stepdown': tool?['max_stepdown'] ?? .125 * unitFactor,
      'side': side,
      'cut_direction': 'climb',
      'entity_ids': m.selection.toList(),
      'breakthrough':
          m.state['setup']?['through_cut_breakthrough'] ?? .015 * unitFactor,
    };
  }

  void cutLine([Json? operation]) {
    final op = operation ?? profileSeed('Cut Line', 'on');
    final defaults = <String, dynamic>{
      for (final k in [
        'name',
        'tool_station',
        'target_depth',
        'safe_z',
        'stepdown',
        'side',
        'cut_direction',
      ])
        k: op[k],
      'cut_intent': op['cut_intent'] ?? 'custom',
      'breakthrough': op['breakthrough'] ?? 0,
      'entry_mode': op['entry_mode'] ?? 'plunge',
      'lead_in': op['lead_in'] ?? 0,
      'lead_out': op['lead_out'] ?? 0,
      'allowance': op['allowance'] ?? 0,
      'pass_depths': op['pass_depths'] ?? <double>[],
    };
    form(
      'Cut Line draft',
      'operation_prepare',
      defaults,
      defaults.keys
          .where(
            (k) => !{
              'name',
              'side',
              'cut_direction',
              'cut_intent',
              'entry_mode',
            }.contains(k),
          )
          .toSet(),
      integers: {'tool_station'},
      numberLists: {'pass_depths'},
      choices: const {
        'side': ['on', 'left', 'right'],
        'cut_direction': ['climb', 'conventional'],
        'cut_intent': ['custom', 'partial', 'through'],
        'entry_mode': ['plunge', 'ramp'],
      },
      extra: {if (op['id'] != null) 'id': op['id']},
      contours: m.entities
          .where(
            (e) =>
                (e['kind'] == 'line' ||
                    e['kind'] == 'polyline' && e['closed'] != true) &&
                m.visible(e) &&
                !m.locked(e),
          )
          .toList(),
      selected: Set<String>.from(op['entity_ids']),
      note:
          'Cut Line follows open lines and polylines. Left/right are relative to the source geometry direction; changing cut direction reverses travel without swapping compensation. On follows the drawn centerline. Through uses current stock thickness plus breakthrough, ignoring entered target depth; stock changes require a new review. Partial must stop above the stock bottom. Pass increments are axial steps; the core adds the remaining depth. Preview the approach, offset and final depth before Apply.',
      onSubmit: (p) => prepareOperation('profile_save', p),
    );
  }

  void advancedProfile(Json op) {
    if (isCutLine(op)) {
      cutLine(op);
      return;
    }
    final defaults = <String, dynamic>{
      for (final k in [
        'name',
        'tool_station',
        'target_depth',
        'safe_z',
        'stepdown',
        'side',
        'cut_direction',
      ])
        k: op[k],
      'contour_order': op['contour_order'] ?? 'inside_first',
      'cut_intent': op['cut_intent'] ?? 'custom',
      'breakthrough': op['breakthrough'] ?? 0,
      'onion_skin': op['onion_skin'] ?? 0,
      'tab_mode': op['tab_mode'] ?? 'even',
      'start_index': op['start_index'] ?? 0,
      'manual_tab_positions': (op['manual_tab_positions'] as List? ?? [])
          .map((v) => number(v) * 100)
          .toList(),
      'remove_tabs_after': op['remove_tabs_after'] ?? false,
      'tab_cleanup_order': op['tab_cleanup_order'] ?? 'end_of_operation',
      'tab_cleanup_min_perimeter': op['tab_cleanup_min_perimeter'] ?? 0,
      'tab_count': op['tab_count'] ?? 0,
      'tab_width': op['tab_width'] ?? 0,
      'tab_height': op['tab_height'] ?? 0,
      'corner_tab_offset': op['corner_tab_offset'] ?? unitFactor,
      'tab_spacing': op['tab_spacing'] ?? 8 * unitFactor,
      'female_tab_min_perimeter':
          op['female_tab_min_perimeter'] ?? 4 * unitFactor,
      'curve_feed_mode': op['curve_feed_mode'] ?? 'off',
      'curve_feed_min_percent': op['curve_feed_min_percent'] ?? 25,
      'curve_feed_transition_distance':
          op['curve_feed_transition_distance'] ?? .5 * unitFactor,
      'common_line_mode': op['common_line_mode'] ?? 'off',
      'common_line_tolerance': op['common_line_tolerance'] ?? .001 * unitFactor,
      'entry_mode': op['entry_mode'] ?? 'plunge',
      'entry_side': op['entry_side'] ?? 'automatic_waste',
      'lead_in': op['lead_in'] ?? 0,
      'lead_out': op['lead_out'] ?? 0,
      'helix_diameter': op['helix_diameter'] ?? 0,
      'helix_pitch': op['helix_pitch'] ?? 0,
      'helix_min_diameter': op['helix_min_diameter'] ?? 0,
      'helix_ramp_angle': op['helix_ramp_angle'] ?? 0,
      'helix_clearance': op['helix_clearance'] ?? 0,
      'helix_finish_revolutions': op['helix_finish_revolutions'] ?? 0,
      'helix_fallback': op['helix_fallback'] ?? 'strict_error',
      'helix_feed_override': op['helix_feed_override'],
      'pass_depths': op['pass_depths'] ?? <double>[],
      'finishing_pass': op['finishing_pass'] ?? false,
      'allowance': op['allowance'] ?? 0,
      'finish_axial_stock': op['finish_axial_stock'] ?? 0,
      'finish_feed_override': op['finish_feed_override'],
      'finish_spindle_rpm_override': op['finish_spindle_rpm_override'],
    };
    form(
      'Profile entry / finishing draft',
      'operation_prepare',
      defaults,
      defaults.keys
          .where(
            (k) => !{
              'name',
              'cut_intent',
              'tab_mode',
              'remove_tabs_after',
              'tab_cleanup_order',
              'contour_order',
              'curve_feed_mode',
              'common_line_mode',
              'side',
              'cut_direction',
              'entry_mode',
              'entry_side',
              'helix_fallback',
              'finishing_pass',
            }.contains(k),
          )
          .toSet(),
      integers: {
        'tool_station',
        'finish_spindle_rpm_override',
        'helix_finish_revolutions',
        'tab_count',
        'start_index',
      },
      booleans: {'finishing_pass', 'remove_tabs_after'},
      numberLists: {'pass_depths', 'manual_tab_positions'},
      optional: {
        'helix_feed_override',
        'finish_feed_override',
        'finish_spindle_rpm_override',
      },
      choices: const {
        'cut_intent': ['custom', 'partial', 'through'],
        'tab_mode': [
          'even',
          'minimum_and_spacing',
          'minimum_avoid_corners',
          'support_aware',
          'corners_and_spacing',
          'manual',
          'aligned',
        ],
        'tab_cleanup_order': ['end_of_operation', 'after_each_part'],
        'contour_order': ['inside_first', 'part_by_part', 'source_order'],
        'curve_feed_mode': ['off', 'wall_compensation'],
        'common_line_mode': ['off', 'paired_rectangles'],
        'side': ['inside', 'outside', 'on'],
        'cut_direction': ['climb', 'conventional'],
        'entry_mode': ['plunge', 'ramp', 'helical'],
        'entry_side': ['automatic_waste', 'inside', 'outside'],
        'helix_fallback': ['strict_error', 'waste_plunge'],
      },
      extra: {if (op['id'] != null) 'id': op['id']},
      contours: closedContours,
      selected: Set<String>.from(op['entity_ids']),
      note:
          'Through uses current stock thickness plus breakthrough, ignoring entered target depth; stock changes require a new review. Partial must stop above the stock bottom. Onion skin remains during roughing and is removed by the final full-depth pass; tabs can remain raised. Even mode with count 0 disables tabs; other modes place tabs automatically using count/spacing. Width means finished retained material; CAM adds the cutter diameter to the raised span. Inside contours below the perimeter threshold receive no tabs. Manual centers are percentages from the selected contour start (0 inclusive, 100 exclusive). Aligned mode shares holding rows/columns across selected outside polygons: use plunge with zero leads; eligible inside cutouts need their own operation. Preferred start is a zero-based vertex or circle quadrant; helix/tab clearance and enabled start optimization may relocate entry. Tab removal releases eligible parts after each part or at the end; aligned removal requires end-of-operation. Smaller perimeters retain tabs. Review the actual cleanup paths and confirm release before Apply. On follows the drawn centerline. Inside first and part by part preserve contained-first release order; source order is a literal advanced override. Curve feed reduces tight-curve speed. Shared edges require exact paired rectangles separated by one cutter diameter, outside plunge cuts, zero allowance/leads and no tabs. Review hold-down warnings and generated block order. Profile increments are axial steps; the core appends the remaining roughing depth when needed. Blank uses maximum stepdown. Helix diameter is the cutter-center orbit. A full-size helix is tried at safe alternative starts before reduction down to the minimum diameter. Pitch and a nonzero angle both limit descent; the tighter limit wins. Clearance adds space from protected geometry. Final-depth revolutions clean the helix floor. Strict fallback stops if no safe helix fits; waste plunge permits only a checked fallback. Review the actual entry location and mode. Allowance reserves wall stock; axial stock reserves the bottom. Zero finish stock produces a spring pass. Review generated entry and final motion before applying.',
      onSubmit: (p) => prepareOperation('profile_save', p),
    );
  }

  void advancedFill([Json? op]) {
    final tool = (data['tools'] as List? ?? [])
        .cast<Json>()
        .where(
          (t) =>
              t['kind'] == 'end_mill' &&
              (op == null || t['station'] == op['tool_station']),
        )
        .firstOrNull;
    final diameter = number(tool?['diameter'] ?? .25 * unitFactor);
    final defaults = <String, dynamic>{
      'name': op?['name'] ?? 'Speed Cleanout',
      'tool_station': op?['tool_station'] ?? tool?['station'] ?? 1,
      'target_depth':
          op?['target_depth'] ??
          m.state['setup']?['material_thickness'] ??
          .125 * unitFactor,
      'safe_z':
          op?['safe_z'] ?? m.state['setup']?['safe_z'] ?? .25 * unitFactor,
      'cut_intent': op?['cut_intent'] ?? 'custom',
      'breakthrough': op?['breakthrough'] ?? 0,
      'corner_tags': op?['corner_tags'] ?? false,
      'stepdown': op?['stepdown'] ?? tool?['max_stepdown'] ?? .125 * unitFactor,
      'stepover': op?['stepover'] ?? diameter * .4,
      'cut_direction': op?['cut_direction'] ?? 'climb',
      'sliver_cleanup': op?['sliver_cleanup'] ?? false,
      'sliver_max_path_length': op?['sliver_max_path_length'] ?? diameter * 4,
      'sliver_link_distance': op?['sliver_link_distance'] ?? 0,
      'pass_mode': op?['pass_mode'] ?? 'maximum_stepdown',
      'pass_count': op?['pass_count'],
      'pass_depths': op?['pass_depths'] ?? <double>[],
      'bottom_finish_pass': op?['bottom_finish_pass'] ?? false,
      'bottom_finish_depth': op?['bottom_finish_depth'] ?? 0,
      'bottom_finish_feed_override': op?['bottom_finish_feed_override'],
      'entry_mode': op?['entry_mode'] ?? 'plunge',
      'ramp_length': op?['ramp_length'] ?? diameter * 2,
      'helix_diameter': op?['helix_diameter'] ?? diameter,
      'helix_min_diameter': op?['helix_min_diameter'] ?? 0,
      'helix_pitch': op?['helix_pitch'] ?? diameter * .25,
      'helix_ramp_angle': op?['helix_ramp_angle'] ?? 0,
      'helix_clearance': op?['helix_clearance'] ?? 0,
      'helix_finish_revolutions': op?['helix_finish_revolutions'] ?? 0,
      'helix_feed_override': op?['helix_feed_override'],
      'helix_fallback': op?['helix_fallback'] ?? 'error',
      'dwell_seconds': op?['dwell_seconds'] ?? 0,
      'keep_tool_down': op?['keep_tool_down'] ?? false,
      'stay_down_max_distance': op?['stay_down_max_distance'] ?? diameter,
      'stay_down_max_engagement':
          op?['stay_down_max_engagement'] ?? diameter * .5,
      'stay_down_feed_percent': op?['stay_down_feed_percent'] ?? 100,
      'strategy': op?['strategy'] ?? 'speed_cleanout',
      'speed_max_full_depth_engagement':
          op?['speed_max_full_depth_engagement'] ?? diameter * .2,
      'speed_center_feed_percent': op?['speed_center_feed_percent'] ?? 60,
      'speed_link_distance': op?['speed_link_distance'] ?? diameter,
      'speed_fallback': op?['speed_fallback'] ?? 'depth_step',
    };
    form(
      op?['strategy'] == 'concentric'
          ? 'Cleanout passes / entry / links'
          : 'Speed Cleanout draft',
      'operation_prepare',
      defaults,
      defaults.keys
          .where(
            (k) => !{
              'name',
              'cut_intent',
              'corner_tags',
              'cut_direction',
              'strategy',
              'speed_fallback',
              'pass_mode',
              'sliver_cleanup',
              'entry_mode',
              'helix_fallback',
              'bottom_finish_pass',
              'keep_tool_down',
              'pass_depths',
            }.contains(k),
          )
          .toSet(),
      integers: {'tool_station', 'pass_count', 'helix_finish_revolutions'},
      numberLists: {'pass_depths'},
      booleans: {
        'bottom_finish_pass',
        'keep_tool_down',
        'sliver_cleanup',
        'corner_tags',
      },
      optional: {
        'pass_count',
        'bottom_finish_feed_override',
        'helix_feed_override',
      },
      choices: const {
        'cut_intent': ['custom', 'partial', 'through'],
        'pass_mode': ['maximum_stepdown', 'equal_count', 'custom_increments'],
        'entry_mode': ['plunge', 'ramp', 'helical'],
        'helix_fallback': ['error', 'ramp', 'plunge'],
        'cut_direction': ['climb', 'conventional', 'bidirectional'],
        'strategy': ['speed_cleanout', 'concentric'],
        'speed_fallback': ['depth_step', 'error'],
      },
      extra: {if (op != null) 'id': op['id']},
      contours: closedContours,
      selected: op == null ? m.selection : Set<String>.from(op['entity_ids']),
      islands: Set<String>.from(op?['island_entity_ids'] ?? []),
      note:
          'Short-loop cleanup defers small residual concentric paths to final depth; links cannot exceed one cutter diameter. Speed Cleanout uses its own center-cleanup strategy. Custom increments must total depth minus reserved floor stock; equal count applies to roughing. Helical entry requires positive orbit, pitch and ramp length (including with error fallback). Keep Tool Down only links after the core proves clearance and engagement. Speed Cleanout depth-steps the walls, then clears proven narrow residual stock at full depth. Engagement and link distance are physical limits. Choose depth_step fallback or block if the proof fails; inspect the resulting motion before Apply.',
      onSubmit: (p) => prepareOperation('fill_save', p),
    );
  }

  Future<void> recipes(Json op, {bool save = false}) async {
    try {
      final library = await m.request('manufacturing.recipe_list');
      if (library['ok'] != true) throw StateError(library['error'].toString());
      if (!mounted) return;
      final entries = (library['recipes'] as List).cast<Json>();
      final name = TextEditingController();
      String? selected, problem;
      int? replacement;
      bool applying = false;
      Json? recalled;
      final source =
          (op['profile_retention']?['edit_inputs']?['source'] as Json?) ?? op;
      final tools = (data['tools'] as List? ?? []).cast<Json>();
      final primary = tools
          .where((t) => t['station'] == source['tool_station'])
          .firstOrNull;
      final smaller = tools
          .where(
            (t) =>
                t['kind'] == 'end_mill' &&
                number(t['diameter']) < number(primary?['diameter']),
          )
          .toList();
      final route = DialogRoute<void>(
        context: context,
        barrierDismissible: false,
        builder: (dialogContext) => StatefulBuilder(
          builder: (context, update) => AlertDialog(
            title: Text(
              save ? 'Save second-tool recipe' : 'Load second-tool recipe',
            ),
            content: SizedBox(
              width: 520,
              child: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'Recipes store second-cutter settings. Loading always creates an unreviewed draft for the current material and geometry.',
                    ),
                    if (save)
                      TextField(
                        controller: name,
                        enabled: !applying,
                        decoration: const InputDecoration(
                          labelText: 'Recipe name',
                        ),
                      )
                    else ...[
                      if (entries.isEmpty)
                        const Text(
                          'No saved recipes. Apply a second-tool group, then save its recipe from the same menu.',
                        ),
                      DropdownButtonFormField<String>(
                        initialValue: selected,
                        decoration: const InputDecoration(
                          labelText: 'Saved recipe',
                        ),
                        items: [
                          for (final item in entries)
                            DropdownMenuItem(
                              value: item['id'] as String,
                              child: Text(item['name'].toString()),
                            ),
                        ],
                        onChanged: applying
                            ? null
                            : (v) => update(() {
                                selected = v;
                                replacement = null;
                                problem = null;
                              }),
                      ),
                      if (selected != null) ...[
                        Text(
                          entries.firstWhere(
                                    (e) => e['id'] == selected,
                                  )['compatible'] ==
                                  true
                              ? 'Saved cutter identity and station match this drawing.'
                              : 'Saved cutter is unavailable or changed. Choose a replacement explicitly.',
                        ),
                        DropdownButtonFormField<int>(
                          key: ValueKey(selected),
                          initialValue: replacement,
                          decoration: const InputDecoration(
                            labelText:
                                'Explicit replacement cutter (optional if matched)',
                          ),
                          items: [
                            for (final tool in smaller)
                              DropdownMenuItem(
                                value: tool['station'] as int,
                                child: Text(
                                  'T${tool['station']} - ${tool['name']} - ${tool['diameter']} ${m.units}',
                                ),
                              ),
                          ],
                          onChanged: applying
                              ? null
                              : (v) => update(() => replacement = v),
                        ),
                      ],
                    ],
                    if (problem != null)
                      Text(
                        problem!,
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                  ],
                ),
              ),
            ),
            actions: [
              TextButton(
                onPressed: applying ? null : () => Navigator.pop(dialogContext),
                child: const Text('Cancel'),
              ),
              if (!save && selected != null)
                TextButton(
                  onPressed: applying
                      ? null
                      : () async {
                          update(() => applying = true);
                          try {
                            final response = await m.request(
                              'manufacturing.recipe_delete',
                              {'id': selected, 'version': library['version']},
                            );
                            if (response['ok'] != true) {
                              throw StateError(response['error'].toString());
                            }
                            if (dialogContext.mounted) {
                              Navigator.pop(dialogContext);
                            }
                          } catch (e) {
                            if (dialogContext.mounted) {
                              update(() {
                                applying = false;
                                problem = e.toString();
                              });
                            }
                          }
                        },
                  child: const Text('Delete saved recipe'),
                ),
              FilledButton(
                onPressed: applying || (!save && selected == null)
                    ? null
                    : () async {
                        update(() => applying = true);
                        try {
                          final response = await m.request(
                            save
                                ? 'manufacturing.recipe_save'
                                : 'manufacturing.recipe_resolve',
                            {
                              'version': library['version'],
                              'operation_id': op['id'],
                              if (save) 'name': name.text else 'id': selected,
                              if (replacement case final int station)
                                'replacement_station': station,
                            },
                          );
                          if (response['ok'] != true) {
                            throw StateError(response['error'].toString());
                          }
                          if (!save) recalled = response;
                          if (dialogContext.mounted) {
                            Navigator.pop(dialogContext);
                          }
                        } catch (e) {
                          if (dialogContext.mounted) {
                            update(() {
                              applying = false;
                              problem = e.toString();
                            });
                          }
                        }
                      },
                child: Text(save ? 'Save recipe' : 'Load settings for review'),
              ),
            ],
          ),
        ),
      );
      await Navigator.of(context).push(route);
      await route.completed;
      name.dispose();
      if (mounted && recalled != null) group(op, null, recalled);
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    }
  }

  void group(Json op, [String? chosenStrategy, Json? recalled]) {
    final hold = (op['profile_retention'] as Json?) ?? {};
    final saved =
        (recalled?['settings'] as Json?) ??
        (hold['edit_inputs']?['settings'] as Json?) ??
        {};
    final source = (hold['edit_inputs']?['source'] as Json?) ?? op;
    final strategy =
        chosenStrategy ?? saved['strategy'] as String? ?? 'retained_skin';
    final tabbed = strategy == 'Tabbed profile + rest / leave X tabs';
    final hybrid = strategy == 'Clear waste + tabbed finish';
    final tools = (data['tools'] as List? ?? []).cast<Json>();
    final main = tools
        .where((t) => t['station'] == source['tool_station'])
        .firstOrNull;
    final smaller = tools
        .where(
          (t) =>
              t['kind'] == 'end_mill' &&
              number(t['diameter']) < number(main?['diameter']),
        )
        .firstOrNull;
    final defaults = <String, dynamic>{
      'station': smaller?['station'] ?? 2,
      'feed_rate': smaller?['feed_rate'] ?? unitFactor,
      'plunge_rate': smaller?['plunge_rate'] ?? .25 * unitFactor,
      'spindle_rpm': smaller?['spindle_rpm'] ?? 18000,
      'stepdown': smaller?['max_stepdown'] ?? .04 * unitFactor,
      'safe_z': source['safe_z'],
      'skin': .025 * unitFactor,
      'ignore_stock_less_than': .001 * unitFactor,
      if (tabbed) 'retain_tabs': 2,
      if (hybrid) ...{
        'main_stepover': number(main?['diameter'] ?? .25 * unitFactor) * .4,
        'stepover': number(smaller?['diameter'] ?? .125 * unitFactor) * .4,
      },
    };
    final values = <String, dynamic>{
      for (final k in [
        'name',
        'tool_station',
        'target_depth',
        'safe_z',
        'stepdown',
        'side',
        'cut_direction',
      ])
        'primary_$k': source[k],
      if (tabbed || hybrid) ...{
        'primary_cut_intent': 'through',
        'primary_breakthrough':
            source['breakthrough'] ??
            m.state['setup']?['through_cut_breakthrough'] ??
            .015 * unitFactor,
        'primary_tab_count': number(source['tab_count']) > 0
            ? source['tab_count']
            : 4,
        'primary_tab_width': number(source['tab_width']) > 0
            ? source['tab_width']
            : .15 * unitFactor,
        'primary_tab_height': number(source['tab_height']) > 0
            ? source['tab_height']
            : .02 * unitFactor,
      },
      for (final e in defaults.entries) e.key: saved[e.key] ?? e.value,
    };
    if (tabbed || hybrid) {
      values.remove('primary_target_depth');
    }
    form(
      tabbed
          ? 'Tabbed profile + rest draft'
          : hybrid
          ? 'Clear waste + tabbed finish draft'
          : 'Retained-skin second-tool draft',
      'group_prepare',
      values,
      values.keys
          .where(
            (k) => !{
              'primary_name',
              'primary_side',
              'primary_cut_direction',
              'primary_cut_intent',
            }.contains(k),
          )
          .toSet(),
      integers: {
        'station',
        'spindle_rpm',
        'primary_tool_station',
        'primary_tab_count',
        'retain_tabs',
      },
      fixed: tabbed || hybrid
          ? {'primary_cut_intent', 'primary_target_depth'}
          : {},
      choices: const {
        'primary_side': ['inside', 'outside'],
        'primary_cut_direction': ['climb', 'conventional'],
      },
      note: tabbed
          ? 'Through depth is recalculated from stock thickness + breakthrough. Both tools preserve the actual shared bridges; cleanup leaves the requested count on each component.'
          : hybrid
          ? 'Clears bounded INSIDE waste above the retained skin, then finishes with the small cutter leaving tabs. Through depth is stock thickness + breakthrough. No automatic release.'
          : null,
      onSubmit: (values) async {
        await request('group_prepare', {
          'id': op['id'],
          if (recalled != null) 'recipe_tool': recalled['recipe_tool'],
          'settings': {
            'strategy': strategy,
            for (final e in values.entries.where(
              (e) => !e.key.startsWith('primary_'),
            ))
              e.key: e.value,
          },
          'source': {
            for (final e in values.entries.where(
              (e) => e.key.startsWith('primary_'),
            ))
              e.key.substring(8): e.value,
          },
        });
        return error == null;
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final ops = (data['operations'] as List? ?? []).cast<Json>();
    final tools = (data['tools'] as List? ?? []).cast<Json>();
    final issues = (result['validation']?['issues'] as List? ?? []);
    final body = LayoutBuilder(
      builder: (context, c) {
        final controlList = ListView(
          controller: inspectorScroll,
          padding: const EdgeInsets.all(12),
          children: [
            Row(
              children: [
                const Expanded(
                  child: Text(
                    "Operations & review",
                    style: TextStyle(fontWeight: FontWeight.bold),
                  ),
                ),
                IconButton(
                  tooltip: "Close inspector",
                  onPressed: () => setState(() => inspectorOpen = false),
                  icon: const Icon(Icons.close),
                ),
              ],
            ),
            if (result['status'] == 'running') ...[
              const LinearProgressIndicator(),
              Text('Generating ${result['progress'] ?? ''}'),
              TextButton(
                onPressed: !requesting ? () => request('cancel') : null,
                child: const Text('Cancel generation'),
              ),
            ],
            Text('Preview: ${result['status'] ?? 'not generated'}'),
            if ((result['knifeFindings'] as List? ?? []).isNotEmpty)
              TextButton.icon(
                onPressed: () => showDialog<void>(
                  context: context,
                  builder: (_) => KnifeClearanceDialog(
                    model: m,
                    findings: (result['knifeFindings'] as List).cast<Json>(),
                  ),
                ),
                icon: const Icon(Icons.location_searching),
                label: Text(
                  'Show knife clearance (${(result['knifeFindings'] as List).length})',
                ),
              ),
            if (result['status'] == 'failed' &&
                result['retryOperation']?['params']?['kind'] == 'drag_knife')
              TextButton(
                onPressed: () {
                  final params = Map<String, dynamic>.from(
                    result['retryOperation']['params'],
                  );
                  specialOperation(
                    'drag_knife',
                    params['id'] == null ? null : params,
                    params,
                  );
                },
                child: const Text('Return to knife settings'),
              ),
            if (result['motionCount'] != null)
              Text(
                '${result['motionCount']} motions. XY overview; select a motion below for Z/feed.',
              ),
            for (final issue in issues)
              ListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                title: Text('${issue['severity']}: ${issue['message']}'),
                subtitle: Text(
                  '${issue['plate'] == null ? '' : "Plate ${issue['plate']} - "}${issue['code'] ?? 'Review'}',
                ),
                trailing: const Icon(Icons.my_location),
                onTap: () => setState(() {
                  focusedIssue = Map<String, dynamic>.from(issue);
                  previewPlate = issue['plate'] as int?;
                }),
              ),
            if (focusedIssue != null) ...[
              Text('Selected finding: ${focusedIssue!['message']}'),
              Text(
                '${(focusedIssue!['entity_ids'] as List? ?? []).length} affected contours highlighted in pink.',
              ),
              Wrap(
                children: [
                  TextButton(
                    onPressed: available && editable ? stock : null,
                    child: const Text('Edit stock'),
                  ),
                  for (final op in ops.where(
                    (o) => (o['entity_ids'] as List).any(
                      (id) => (focusedIssue!['entity_ids'] as List? ?? [])
                          .contains(id),
                    ),
                  ))
                    TextButton(
                      onPressed: available && editable
                          ? () => operationEditor(op)
                          : null,
                      child: Text('Edit ${op['name']}'),
                    ),
                  TextButton(
                    onPressed: () => setState(() => focusedIssue = null),
                    child: const Text('Clear highlight'),
                  ),
                ],
              ),
            ],
            if (result['requiresKnifeReview'] == true)
              CheckboxListTile(
                value: knifeReviewed,
                onChanged: (v) => setState(() => knifeReviewed = v == true),
                title: const Text(
                  'I reviewed edge-aware knife motion and verified corner separation on scrap.',
                ),
              ),
            if (result['requiresReleaseReview'] == true)
              CheckboxListTile(
                value: releaseReviewed,
                onChanged: (v) => setState(() => releaseReviewed = v == true),
                title: const Text(
                  'I reviewed tab-removal motion and the release of parts.',
                ),
              ),
            if (result['status'] == 'management_ready') ...[
              if (result['details']['small_centers'] != null)
                Text(
                  'Small-center cleanout: ${(result['details']['small_centers']['selected'] as List).length} selected. Review the center-out ramping before the parent profile. No separate operation is created.',
                ),
              for (final row
                  in result['details']['linked_changes'] as List? ?? [])
                linkedReview(row as Json),
              if (result['details']['graphical'] != null)
                graphicalReview(
                  result['details']['graphical'] as Json,
                  m.units,
                ),
              if (result['details']['keepout_change'] != null)
                keepoutReview(result['details']['keepout_change'] as Json),
              if (result['details']['final_release'] != null)
                releaseDetails(result['details']['final_release'] as Json),
              if (result['action'] == 'turret') ...[
                Text(
                  'Changed stations: ${result['details']['changed_stations']}',
                ),
                Text(
                  'Affected operations: ${result['details']['affected_operations'] ?? []}',
                ),
                Text(
                  'Machine RPM limits: ${result['details']['machine_rpm'] ?? []}',
                ),
              ],
              Text(
                'Review ${result['action'].toString().replaceAll('_', ' ')}',
              ),
              if (result['details']['rebuilt_groups'] != null)
                Text(
                  '${result['details']['rebuilt_groups']} linked workflows regenerated from saved source inputs.',
                ),
              if (result['details']['revision'] != null)
                Text(
                  'Revised DXF: ${jsonEncode(result['details']['revision'])}',
                ),
              for (final row in result['details']['geometry_changes'] as List)
                Text(
                  '${row['name']}: ${(row['before'] as List).length} → ${(row['after'] as List).length} source contours. Rule: ${jsonEncode(row['selector'])}',
                ),
              if (result['details']['entity_sides'] != null)
                Text(
                  'Per-contour compensation: ${jsonEncode(result['details']['entity_sides'])}',
                ),
              if (result['details']['manual_contour_order'] != null)
                Text(
                  'Requested contour order: ${result['details']['manual_contour_order']}',
                ),
              if (result['details']['retained_cleanup'] != null)
                Text(
                  'Retained tab plan: ${jsonEncode(result['details']['retained_cleanup'])}',
                ),
              const Text('Resulting operation order:'),
              for (final row in result['details']['operation_order'] as List)
                Text(
                  '${row['name']} · ${row['enabled'] == false ? 'disabled' : 'enabled'} · ${(row['prerequisites'] as List).length} prerequisites',
                ),
              CheckboxListTile(
                value: reviewed,
                onChanged: (v) => setState(() => reviewed = v == true),
                title: const Text(
                  'I reviewed the source geometry, operation order and regenerated motion.',
                ),
              ),
              FilledButton(
                onPressed:
                    available &&
                        reviewed &&
                        releaseAllowed &&
                        result['action'] != 'release_audit'
                    ? () => edit('management_apply', {
                        'token': result['token'],
                        'reviewed': true,
                        'release_reviewed': releaseReviewed,
                        if (result['requiresKnifeReview'] == true)
                          'knife_reviewed': knifeReviewed,
                      })
                    : null,
                child: const Text('Apply workflow draft'),
              ),
              TextButton(
                onPressed: available ? () => request('cancel') : null,
                child: const Text('Discard workflow draft'),
              ),
            ],
            if (result['status'] == 'optimization_ready') ...[
              const Text('Estimated cycle comparison'),
              for (final metric in <String, String>{
                'estimated_seconds': 'Time (seconds)',
                'rapid_distance': 'Rapid travel (${m.units})',
                'motions': 'Motions',
                'plunges': 'Plunges',
                'retracts': 'Retracts',
                'depth_links': 'Depth links',
                'reordered_blocks': 'Reordered blocks',
                'compacted_motions': 'Compacted motions',
              }.entries)
                Text(
                  '${metric.value}: original ${result['comparison']['baseline'][metric.key]} → requested ${result['comparison']['optimized'][metric.key]}',
                ),
              SwitchListTile(
                value: showBaseline,
                onChanged: (v) => setState(() => showBaseline = v),
                title: const Text('Show original motion'),
              ),
              CheckboxListTile(
                value: reviewed,
                onChanged: (v) => setState(() => reviewed = v == true),
                title: const Text(
                  'I compared the original and requested motion and settings.',
                ),
              ),
              FilledButton(
                onPressed: available && reviewed && releaseAllowed
                    ? () => edit('optimization_apply', {
                        'token': result['token'],
                        'reviewed': true,
                        'release_reviewed': releaseReviewed,
                        if (result['requiresKnifeReview'] == true)
                          'knife_reviewed': knifeReviewed,
                      })
                    : null,
                child: const Text('Apply optimization draft'),
              ),
              TextButton(
                onPressed: available ? () => request('cancel') : null,
                child: const Text('Discard optimization draft'),
              ),
            ],
            if (result['status'] == 'group_ready') ...[
              Text(
                result['workflow'] == 'tabbed_profile_rest'
                    ? 'Shared-tab rest and cleanup; surviving bridges stay in place.'
                    : result['workflow'] == 'waste_then_tabbed_finish'
                    ? 'Bounded waste clearing above skin, followed by through-depth tabbed finish. No automatic release.'
                    : 'Retained-skin profile group; no automatic release.',
              ),
              if (result['workflow'] != 'tabbed_profile_rest')
                Text(
                  'Clearing floor retains ${result['skin']} ${m.units} of ${result['thickness']} ${m.units} stock.',
                ),
              for (final stage in result['stages'] as List)
                Text(
                  '${stage['name']}: T${stage['tool_station']}, depth ${stage['target_depth']} ${m.units}; ${stage['depends_on_ids'].length} prerequisites',
                ),
              CheckboxListTile(
                value: reviewed,
                onChanged: (v) => setState(() => reviewed = v == true),
                title: const Text(
                  'I reviewed retained material, linked stages, cutter feeds/reach and motion.',
                ),
              ),
              FilledButton(
                onPressed: available && reviewed && releaseAllowed
                    ? () => edit('group_apply', {
                        'token': result['token'],
                        'reviewed': true,
                        'release_reviewed': releaseReviewed,
                        if (result['requiresKnifeReview'] == true)
                          'knife_reviewed': knifeReviewed,
                      })
                    : null,
                child: const Text('Apply complete group'),
              ),
              TextButton(
                onPressed: available ? () => request('cancel') : null,
                child: const Text('Discard group draft'),
              ),
            ],
            if (result['status'] == 'operation_ready') ...[
              if (result['attachment'] != null) ...[
                Text(
                  'Recipe: ${result['attachment']['match']['name']} · ${result['attachment']['match']['qualification']}',
                ),
                Text('${result['attachment']['note']}'),
                for (final change
                    in (result['attachment']['changes'] as List).cast<Json>())
                  if (change['changed'] == true)
                    Text(
                      '${change['field']}: ${change['before']} → ${change['after']}',
                    ),
              ],
              Text(
                'Draft: ${result['operation']['name']} - ${result['operation']['entry_mode'] ?? result['operation']['strategy'] ?? result['operation']['peck_mode']}',
              ),
              if (result['drillSchedule'] != null)
                Text('Core peck depths: ${result['drillSchedule']} ${m.units}'),
              if (result['blockReview'] != null)
                ExpansionTile(
                  title: Text(
                    'Generated block order / details (${result['blockCount']})',
                  ),
                  children: [
                    for (final block in result['blockReview'] as List)
                      ListTile(
                        dense: true,
                        title: Text(block['label'].toString()),
                        subtitle: Text(blockDetails(block['details'] as Json)),
                      ),
                    if (number(result['blockCount']) > 200)
                      const Text(
                        'First 200 blocks shown; inspect the motion preview for the full path.',
                      ),
                  ],
                ),
              if (result['schedule'] != null)
                Text(
                  'Core pass schedule: ${result['schedule']} (${m.units}; true marks floor finishing).',
                ),
              Text(
                'Depth ${result['operation']['target_depth']} ${m.units}; safe Z ${result['operation']['safe_z']} ${m.units}',
              ),
              if (result['operation']['kind'] == 'router_offset')
                Text(
                  'Depth intent ${result['operation']['cut_intent']}; breakthrough ${result['operation']['breakthrough']} ${m.units}. Roughing onion skin ${result['operation']['onion_skin']} ${m.units} is removed by the final pass. Tabs ${result['operation']['tab_mode']}, requested ${result['operation']['tab_count']}, finished width ${result['operation']['tab_width']}, retained height ${result['operation']['tab_height']} ${m.units}. Tab removal ${result['operation']['remove_tabs_after'] == true ? 'ON: parts may be released' : 'off'}, timing ${result['operation']['tab_cleanup_order']}, minimum perimeter ${result['operation']['tab_cleanup_min_perimeter']} ${m.units}. Preferred start ${result['operation']['start_index']}; manual centers ${(result['operation']['manual_tab_positions'] as List? ?? []).map((v) => number(v) * 100).toList()}%. Review actual raised paths, cleanup and final depth in the preview.',
                ),
              Text(
                result['operation']['kind'] == 'drill_circles'
                    ? 'Pecks ${result['operation']['peck_definition']}; mode ${result['operation']['peck_mode']}; feed retract ${result['operation']['controlled_retract']}; return to surface ${result['operation']['return_to_surface']}. Approach ${result['operation']['surface_feed_override'] ?? 'cutter'}, drill ${result['operation']['plunge_rate_override'] ?? 'cutter'}, retract ${result['operation']['retract_feed_override'] ?? 'cutter'} ${m.units}/s; RPM ${result['operation']['spindle_rpm_override'] ?? 'cutter'}.'
                    : result['operation']['kind'] == 'router_offset'
                    ? 'Order ${result['operation']['contour_order']}; curve feed ${result['operation']['curve_feed_mode']} (minimum ${result['operation']['curve_feed_min_percent']}%); common line ${result['operation']['common_line_mode']}. Entry side ${result['operation']['entry_side']}; leads ${result['operation']['lead_in']} / ${result['operation']['lead_out']} ${m.units}; helix orbit ${result['operation']['helix_diameter']}, minimum ${result['operation']['helix_min_diameter'] ?? 0}, pitch ${result['operation']['helix_pitch']} ${m.units}; angle ${result['operation']['helix_ramp_angle'] ?? 0} degrees, clearance ${result['operation']['helix_clearance'] ?? 0} ${m.units}, final-depth revolutions ${result['operation']['helix_finish_revolutions'] ?? 0}; fallback ${result['operation']['helix_fallback']}. Finish ${result['operation']['finishing_pass']}; wall stock ${result['operation']['allowance']}, bottom stock ${result['operation']['finish_axial_stock']} ${m.units}; finish feed ${result['operation']['finish_feed_override'] ?? 'cutter default'}, RPM ${result['operation']['finish_spindle_rpm_override'] ?? 'cutter default'}.'
                    : 'Short-loop cleanup ${result['operation']['sliver_cleanup']}, maximum length ${result['operation']['sliver_max_path_length']}, link ${result['operation']['sliver_link_distance']} ${m.units}. Entry ${result['operation']['entry_mode']}, ramp ${result['operation']['ramp_length']}, helix orbit ${result['operation']['helix_diameter']}, pitch ${result['operation']['helix_pitch']} ${m.units}, fallback ${result['operation']['helix_fallback']}. Keep Tool Down ${result['operation']['keep_tool_down']}, link distance ${result['operation']['stay_down_max_distance']}, engagement ${result['operation']['stay_down_max_engagement']} ${m.units}, feed ${result['operation']['stay_down_feed_percent']}%. Stepdown ${result['operation']['stepdown']}, stepover ${result['operation']['stepover']} ${m.units}; full-depth engagement ${result['operation']['speed_max_full_depth_engagement']} ${m.units}, center feed ${result['operation']['speed_center_feed_percent']}%, link limit ${result['operation']['speed_link_distance']} ${m.units}; fallback ${result['operation']['speed_fallback']}.',
              ),
              CheckboxListTile(
                value: reviewed,
                onChanged: (v) => setState(() => reviewed = v == true),
                title: const Text(
                  'I reviewed the entry, depth passes, finishing/cleanout settings and motion.',
                ),
              ),
              FilledButton(
                onPressed: available && reviewed && releaseAllowed
                    ? () => edit('operation_apply', {
                        'token': result['token'],
                        'reviewed': true,
                        'release_reviewed': releaseReviewed,
                        if (result['requiresKnifeReview'] == true)
                          'knife_reviewed': knifeReviewed,
                      })
                    : null,
                child: Text(
                  result['attachment'] != null
                      ? 'Apply recipe attachment'
                      : 'Apply operation draft',
                ),
              ),
              TextButton(
                onPressed: available ? () => request('cancel') : null,
                child: const Text('Discard operation draft'),
              ),
            ],
            if ((result['previews'] as List? ?? []).length > 1)
              DropdownButton<int>(
                value:
                    previewPlate ??
                    result['previews'][0]['plate']['number'] as int,
                items: [
                  for (final p in result['previews'] as List)
                    DropdownMenuItem(
                      value: p['plate']['number'] as int,
                      child: Text('Preview plate ${p['plate']['number']}'),
                    ),
                ],
                onChanged: (v) => setState(() {
                  previewPlate = v;
                  focusedIssue = null;
                }),
              ),
            if (result['errors'] != null) Text(result['errors'].toString()),
            if (result['error'] != null) Text(result['error'].toString()),
            if (error != null)
              SelectableText(
                error!,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            if (result['status'] == 'ready') ...[
              if ((result['openPathOvertravel'] as List? ?? []).isNotEmpty) ...[
                const Text('Cuts beyond stock'),
                for (final row in result['openPathOvertravel'] as List)
                  Text(
                    'Plate ${row['owner']}: ${row['id']} · ${(row['operations'] as List).join(', ')}',
                  ),
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  value: overtravelReviewed,
                  onChanged: (v) =>
                      setState(() => overtravelReviewed = v == true),
                  title: const Text(
                    'I reviewed the complete cuts, machine travel and fixture clearance beyond stock.',
                  ),
                ),
              ],
              for (final r in (result['outputRanges'] as List? ?? []))
                Text(
                  'Plate ${r['plate']} cutter-center range (min X, min Y, max X, max Y): ${r['bounds']} ${m.units}',
                ),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                value: allowNegativeXY,
                onChanged: (v) => setState(() => allowNegativeXY = v == true),
                title: const Text(
                  'Allow negative X/Y for this export; I verified the displayed range against the machine setup.',
                ),
              ),
              if (result['plates'] != null)
                Text(
                  'Reviewed output plates: ${(result['plates'] as List).join(', ')}',
                ),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                value: reviewed,
                onChanged: (v) => setState(() => reviewed = v == true),
                title: const Text(
                  'I reviewed the toolpath motion and preflight messages.',
                ),
              ),
              TextField(
                controller: packageName,
                decoration: const InputDecoration(
                  labelText: 'Output package name',
                ),
              ),
              const Text(
                'Creates an evaluation CNC + CAM package under this prototype\'s build/manufacturing-output folder. No machine delivery.',
              ),
              FilledButton(
                onPressed:
                    available &&
                        reviewed &&
                        releaseAllowed &&
                        ((result['openPathOvertravel'] as List? ?? [])
                                .isEmpty ||
                            overtravelReviewed)
                    ? () => request('export', {
                        'token': result['token'],
                        'reviewed': true,
                        'release_reviewed': releaseReviewed,
                        if (result['requiresKnifeReview'] == true)
                          'knife_reviewed': knifeReviewed,
                        'overtravel_reviewed': overtravelReviewed,
                        'allow_negative_xy': allowNegativeXY,
                        'name': packageName.text.trim(),
                      })
                    : null,
                child: const Text('Export reviewed package'),
              ),
            ],
            for (final path in result['paths'] as List? ?? [])
              SelectableText(path.toString()),
            Text(
              data['scope']?.toString() ??
                  'Restart the backend to load Manufacturing.',
            ),
            if (!editable)
              const Text(
                'Advanced operations are protected. Open a Design drawing or a basic-profile copy.',
              ),
            const SizedBox(height: 12),
            FilledButton.tonal(
              onPressed: available && editable ? stock : null,
              child: const Text('Stock / job setup'),
            ),
            Text(
              '${m.state['setup']?['material'] ?? 'Material not set'}; thickness ${m.state['setup']?['material_thickness'] ?? '-'} ${m.units}; safe Z ${m.state['setup']?['safe_z'] ?? '-'} ${m.units}',
            ),
            CheckboxListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              title: const Text('Require verified physical stock for export'),
              value: data['stockVerification']?['required'] == true,
              onChanged: available && editable
                  ? (v) => edit('setup', {'require_verified_stock': v == true})
                  : null,
            ),
            const Divider(),
            Row(
              children: [
                const Expanded(child: Text('Cutters')),
                TextButton(
                  onPressed: available && editable ? cutter : null,
                  child: const Text('Add cutter'),
                ),
              ],
            ),
            for (final t in tools)
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: Text('T${t['station']} - ${t['name']}'),
                subtitle: Text(
                  '${t['diameter']} ${m.units}; feed ${t['feed_rate']} ${m.units}/s',
                ),
                onTap: available && editable ? () => cutter(t) : null,
                trailing: IconButton(
                  tooltip: 'Delete unused cutter',
                  onPressed: available && editable
                      ? () => edit('tool_delete', {'station': t['station']})
                      : null,
                  icon: const Icon(Icons.delete_outline),
                ),
              ),
            const Divider(),
            Row(
              children: [
                const Expanded(child: Text('Operations')),
                TextButton(
                  onPressed: available && editable ? profile : null,
                  child: const Text('Add profile'),
                ),
              ],
            ),
            Wrap(
              spacing: 8,
              children: [
                TextButton(
                  onPressed: available && editable
                      ? () => machining('drill')
                      : null,
                  child: const Text('Add drilling'),
                ),
                TextButton(
                  onPressed: available && editable
                      ? () => machining('fill')
                      : null,
                  child: const Text('Add cleanout'),
                ),
              ],
            ),
            TextButton(
              onPressed: available && editable ? () => advancedFill() : null,
              child: const Text('Add Speed Cleanout'),
            ),
            Wrap(
              spacing: 8,
              children: [
                TextButton(
                  onPressed: available && editable
                      ? () => advancedProfile(
                          profileSeed('Profile with holding', 'outside'),
                        )
                      : null,
                  child: const Text('Add profile with holding'),
                ),
                TextButton(
                  onPressed: available && editable ? () => cutLine() : null,
                  child: const Text('Add Cut Line'),
                ),
                TextButton(
                  onPressed: available && editable
                      ? () =>
                            advancedProfile(profileSeed('On-contour cut', 'on'))
                      : null,
                  child: const Text('Add on-contour cut'),
                ),
              ],
            ),
            for (final op in ops)
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Checkbox(
                  value: op['enabled'] == true,
                  onChanged: available && editable
                      ? (_) => edit('operation_toggle', {'id': op['id']})
                      : null,
                ),
                title: Text(op['name'].toString()),
                subtitle: Text(
                  'T${op['tool_station']} - ${op['side'] ?? op['kind']} - depth ${op['target_depth']} ${m.units}',
                ),
                onTap: available && editable ? () => operationEditor(op) : null,
                trailing: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (op['kind'] == 'router_offset' &&
                            ['inside', 'outside'].contains(op['side']) &&
                            !isCutLine(op) ||
                        (op['profile_retention'] as Map?)?.isNotEmpty == true)
                      PopupMenuButton<String>(
                        tooltip: 'Second-tool workflow',
                        icon: const Icon(Icons.account_tree_outlined),
                        enabled: available && editable,
                        onSelected: (v) => v == '__load'
                            ? recipes(op)
                            : v == '__save'
                            ? recipes(op, save: true)
                            : group(op, v),
                        itemBuilder: (_) => [
                          const PopupMenuItem(
                            value: '__load',
                            child: Text('Load second-tool recipe...'),
                          ),
                          if ((op['profile_retention'] as Map? ?? {})
                              .isNotEmpty)
                            const PopupMenuItem(
                              value: '__save',
                              child: Text('Save second-tool recipe...'),
                            ),
                          const PopupMenuItem(
                            value: 'retained_skin',
                            child: Text('Retained skin'),
                          ),
                          const PopupMenuItem(
                            value: 'Tabbed profile + rest / leave X tabs',
                            child: Text('Tabbed profile + rest'),
                          ),
                          const PopupMenuItem(
                            value: 'Clear waste + tabbed finish',
                            child: Text('Clear waste + tabbed finish'),
                          ),
                        ],
                      ),
                    if (op['kind'] == 'drill_circles')
                      IconButton(
                        tooltip: 'Drilling cycle',
                        icon: const Icon(Icons.tune),
                        onPressed: available && editable
                            ? () => advancedDrill(op)
                            : null,
                      ),
                    if (op['kind'] == 'island_fill' &&
                        (op['profile_retention'] as Map? ?? {}).isEmpty)
                      IconButton(
                        tooltip: 'Cleanout passes / entry / links',
                        icon: const Icon(Icons.tune),
                        onPressed: available && editable
                            ? () => advancedFill(op)
                            : null,
                      ),
                    if (op['kind'] == 'router_offset' &&
                        (op['profile_retention'] as Map? ?? {}).isEmpty)
                      IconButton(
                        tooltip: 'Profile entry / finishing',
                        icon: const Icon(Icons.tune),
                        onPressed: available && editable
                            ? () => advancedProfile(op)
                            : null,
                      ),
                    if ((op['profile_retention'] as Map? ?? {}).isNotEmpty)
                      PopupMenuButton<String>(
                        tooltip: 'Linked group workflow',
                        enabled: available && editable,
                        onSelected: (v) => management(v, op),
                        itemBuilder: (_) => [
                          for (final entry in linkedActions.entries)
                            PopupMenuItem(
                              value: entry.key,
                              child: Text(entry.value),
                            ),
                        ],
                      ),
                    if ((op['profile_retention'] as Map? ?? {}).isEmpty)
                      PopupMenuButton<String>(
                        tooltip: 'Operation workflow',
                        enabled: available && editable,
                        onSelected: (v) => management(v, op),
                        itemBuilder: (_) => [
                          if (data['smallCenters'] == true &&
                              op['kind'] == 'router_offset' &&
                              !isCutLine(op))
                            const PopupMenuItem(
                              value: 'small_centers',
                              child: Text('Small-center cleanout'),
                            ),
                          if (op['kind'] == 'router_offset' && !isCutLine(op))
                            for (final kind in ['start', 'tabs', 'order'])
                              PopupMenuItem(
                                value: 'pick_$kind',
                                child: Text(pickingNames[kind]!),
                              ),
                          if (op['kind'] == 'island_fill')
                            for (final kind in ['boundaries', 'islands'])
                              PopupMenuItem(
                                value: 'pick_$kind',
                                child: Text(pickingNames[kind]!),
                              ),
                          if (op['kind'] == 'router_offset' && !isCutLine(op))
                            const PopupMenuItem(
                              value: 'profile_controls',
                              child: Text('Contour compensation / order'),
                            ),
                          if (op['kind'] == 'router_offset' && !isCutLine(op))
                            const PopupMenuItem(
                              value: 'retained_cleanup',
                              child: Text('Plan retained tab cleanup'),
                            ),
                          if ((op['tab_release_plan'] as Map? ?? {}).isNotEmpty)
                            const PopupMenuItem(
                              value: 'keep_tabs',
                              child: Text('Keep all original tabs'),
                            ),
                          const PopupMenuItem(
                            value: 'sequence',
                            child: Text('Sequence / prerequisites'),
                          ),
                          PopupMenuItem(
                            value: 'associate',
                            child: Text(
                              (op['geometry_selector'] as Map? ?? {}).isEmpty
                                  ? 'Bind geometry selection rule'
                                  : 'Remove geometry selection rule',
                            ),
                          ),
                        ],
                      ),
                    IconButton(
                      tooltip: 'Delete operation or whole linked group',
                      onPressed: available && editable
                          ? () => edit('operation_delete', {'id': op['id']})
                          : null,
                      icon: const Icon(Icons.delete_outline),
                    ),
                  ],
                ),
              ),
            const Divider(),
            TextButton.icon(
              onPressed:
                  available &&
                      ops.any(
                        (o) =>
                            (o['profile_retention'] as Map? ?? {}).isNotEmpty,
                      )
                  ? () => management('import_groups')
                  : null,
              icon: const Icon(Icons.verified_outlined),
              label: const Text('Verify imported linked workflows'),
            ),
            TextButton.icon(
              onPressed: available && editable && ops.isNotEmpty
                  ? () => management('revision')
                  : null,
              icon: const Icon(Icons.file_open_outlined),
              label: const Text('Review revised DXF'),
            ),
            TextButton.icon(
              onPressed: available && editable && ops.isNotEmpty
                  ? optimization
                  : null,
              icon: const Icon(Icons.speed),
              label: const Text('Compare cycle optimization'),
            ),
            Text(
              data['optimization']?['enabled'] == true
                  ? 'Cycle optimization enabled'
                  : 'Original generated motion',
            ),
            if (data['closedOverruns'] == true)
              TextButton.icon(
                onPressed: available && editable
                    ? () => showDialog<void>(
                        context: context,
                        builder: (_) => ClosedOverrunsDialog(model: m),
                      )
                    : null,
                icon: const Icon(Icons.crop_free),
                label: const Text('Closed-outline plate overruns'),
              ),
            if (data['openPathOwnership'] == true)
              TextButton.icon(
                onPressed: available && editable
                    ? () => showDialog<void>(
                        context: context,
                        builder: (_) => OpenPathPlatesDialog(model: m),
                      )
                    : null,
                icon: const Icon(Icons.call_split),
                label: const Text('Open-path plate assignments'),
              ),
            if (m.plates.length > 1) ...[
              const Text('Plates to generate / export'),
              for (final plate in m.plates)
                CheckboxListTile(
                  dense: true,
                  title: Text(
                    'Plate ${plate['number']} - ${plate['name'] ?? ''}',
                  ),
                  value: outputPlates.contains(plate['number']),
                  onChanged: available
                      ? (v) {
                          request('cancel');
                          setState(() {
                            if (v == true) {
                              outputPlates.add(plate['number'] as int);
                            } else {
                              outputPlates.remove(plate['number']);
                            }
                            reviewed = false;
                            overtravelReviewed = false;
                            allowNegativeXY = false;
                          });
                        }
                      : null,
                ),
            ],
            FilledButton(
              onPressed: available && editable
                  ? () => request('generate', {'plates': outputPlates.toList()})
                  : null,
              child: const Text('Generate and check toolpaths'),
            ),
          ],
        );
        final controls = Material(color: widget.panelColor, child: controlList);
        final previews = (result['previews'] as List? ?? []).cast<Json>();
        final shown =
            previews
                .where((p) => p['plate']['number'] == previewPlate)
                .firstOrNull ??
            previews.firstOrNull ??
            result;
        final viewSwitch = Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            TextButton.icon(
              onPressed: () => setState(() => showDrawing = true),
              icon: Icon(
                showDrawing
                    ? Icons.radio_button_checked
                    : Icons.radio_button_unchecked,
              ),
              label: const Text('Drawing'),
            ),
            TextButton.icon(
              onPressed: () => setState(() => showDrawing = false),
              icon: Icon(
                showDrawing
                    ? Icons.radio_button_unchecked
                    : Icons.radio_button_checked,
              ),
              label: const Text('Toolpaths'),
            ),
          ],
        );
        final preview = Column(
          children: [
            if (widget.shellBuilder == null) viewSwitch,
            Expanded(
              child: IndexedStack(
                index: showDrawing ? 0 : 1,
                children: [
                  ManufacturingDrawing(
                    model: m,
                    enabled: available,
                    view: widget.view,
                    panelColor: widget.panelColor,
                    header: widget.documentBarBuilder?.call(viewSwitch),
                  ),
                  Column(
                    children: [
                      if (widget.documentBarBuilder != null)
                        Padding(
                          padding: const EdgeInsets.only(left: 45),
                          child: widget.documentBarBuilder!(viewSwitch),
                        ),
                      Expanded(
                        child: MotionPreview(
                          result: {
                            ...shown,
                            if (result['status'] == 'optimization_ready' &&
                                showBaseline)
                              'motions': result['baselineMotions'],
                            'focusedIssue': focusedIssue,
                          },
                          units: m.units,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ],
        );
        final pane = SizedBox(
          width: widget.shellBuilder != null
              ? 246
              : math.min(420, c.maxWidth * .46),
          child: controls,
        );
        final actions = <String, VoidCallback?>{
          'save': editable
              ? () {
                  widget.onSave();
                }
              : null,
          'readiness': m.state['guidedReadiness'] == true
              ? guidedReadiness
              : null,
          'stock': editable ? stock : null,
          'library': () => toolsDialog(library: true),
          'turret': editable ? () => toolsDialog() : null,
          'keepouts': editable ? keepoutsDialog : null,
          'pick': editable ? pickingMenu : null,
          'outside': editable
              ? () => advancedProfile(profileSeed('Outside profile', 'outside'))
              : null,
          'inside': editable
              ? () => advancedProfile(profileSeed('Inside profile', 'inside'))
              : null,
          'line': editable ? () => cutLine() : null,
          'fill': editable ? () => machining('fill') : null,
          'drill': editable ? () => machining('drill') : null,
          'engrave': editable ? () => specialOperation('engrave') : null,
          'special': editable ? specialMenu : null,
          'optimization': editable && ops.isNotEmpty ? optimization : null,
          'inspect': () => setState(() => inspectorOpen = !inspectorOpen),
          'output': () => setState(() => inspectorOpen = true),
          'generate': editable
              ? () => request('generate', {'plates': outputPlates.toList()})
              : null,
        };
        final canvas = c.maxWidth >= 850
            ? Row(
                children: [
                  Expanded(child: preview),
                  if (inspectorOpen) ...[
                    if (widget.shellBuilder == null)
                      const VerticalDivider(width: 1),
                    pane,
                  ],
                ],
              )
            : Column(
                children: [
                  Expanded(child: preview),
                  if (inspectorOpen)
                    SizedBox(
                      height: math.min(300, c.maxHeight * .4),
                      child: controls,
                    ),
                ],
              );
        if (widget.shellBuilder != null) {
          return widget.shellBuilder!({
            for (final e in actions.entries) e.key: available ? e.value : null,
          }, canvas);
        }
        return Column(
          children: [
            ManufacturingRibbon(model: m, enabled: available, actions: actions),
            Expanded(child: canvas),
            SizedBox(
              height: 28,
              child: Row(
                children: [
                  const SizedBox(width: 8),
                  const Text('HISTORY', style: TextStyle(fontSize: 10)),
                  IconButton(
                    padding: EdgeInsets.zero,
                    tooltip: 'Manufacturing undo',
                    onPressed: available && m.state['canUndo'] == true
                        ? () => m.run('history.undo')
                        : null,
                    icon: const Icon(Icons.undo, size: 17),
                  ),
                  IconButton(
                    padding: EdgeInsets.zero,
                    tooltip: 'Manufacturing redo',
                    onPressed: available && m.state['canRedo'] == true
                        ? () => m.run('history.redo')
                        : null,
                    icon: const Icon(Icons.redo, size: 17),
                  ),
                  Expanded(
                    child: Text(
                      error ??
                          result['error']?.toString() ??
                          '${ops.length} operations · ${tools.length} loaded tools · ${result['status'] ?? 'Select drawing geometry to begin'}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 11),
                    ),
                  ),
                  const SizedBox(width: 8),
                ],
              ),
            ),
          ],
        );
      },
    );
    if (widget.shellBuilder != null) return body;
    return Dialog.fullscreen(
      child: Scaffold(
        appBar: AppBar(
          leading: IconButton(
            tooltip: 'Back to Design',
            icon: const Icon(Icons.arrow_back),
            onPressed: requesting ? null : () => Navigator.pop(context),
          ),
          toolbarHeight: 34,
          title: Row(
            children: [
              TextButton(
                onPressed: requesting ? null : () => Navigator.pop(context),
                child: const Text('DESIGN'),
              ),
              const SizedBox(width: 12),
              const Text(
                'MANUFACTURING',
                style: TextStyle(fontSize: 13, fontWeight: FontWeight.bold),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Text(
                  '${m.state['name'] ?? 'Drawing'}',
                  style: const TextStyle(fontSize: 12),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
          actions: [
            IconButton(
              tooltip: 'Undo',
              onPressed: available && editable && m.state['canUndo'] == true
                  ? () => m.run('history.undo')
                  : null,
              icon: const Icon(Icons.undo),
            ),
            IconButton(
              tooltip: 'Redo',
              onPressed: available && editable && m.state['canRedo'] == true
                  ? () => m.run('history.redo')
                  : null,
              icon: const Icon(Icons.redo),
            ),
            TextButton(
              onPressed: available && editable ? widget.onSave : null,
              child: const Text('Save job as'),
            ),
          ],
        ),
        body: body,
      ),
    );
  }
}

class MotionPreview extends StatefulWidget {
  const MotionPreview({super.key, required this.result, required this.units});
  final Json result;
  final String units;
  @override
  State<MotionPreview> createState() => _MotionPreviewState();
}

class _MotionPreviewState extends State<MotionPreview> {
  int selected = 0;
  @override
  Widget build(BuildContext context) {
    final blocks = (widget.result['motions'] as List? ?? [])
        .map((b) => (b as List).cast<Json>())
        .toList();
    final motions = blocks.expand((b) => b).toList();
    selected = selected.clamp(0, math.max(0, motions.length - 1));
    if (motions.isEmpty &&
        (widget.result['geometry'] as List? ?? []).isEmpty &&
        (widget.result['keepouts'] as List? ?? []).isEmpty) {
      return const Center(
        child: Text('Generate toolpaths to inspect the actual cutter motion.'),
      );
    }
    final motion = motions.isEmpty ? <String, dynamic>{} : motions[selected];
    return Column(
      children: [
        const Padding(
          padding: EdgeInsets.all(12),
          child: Text(
            'Plate-local XY. Teal: cutting; orange: rapid; gray: drawing. Scroll to zoom; drag to pan.',
          ),
        ),
        Expanded(
          child: InteractiveViewer(
            minScale: .5,
            maxScale: 12,
            child: CustomPaint(
              painter: MotionPainter(
                blocks,
                motion,
                (widget.result['geometry'] as List? ?? []).cast<Json>(),
                widget.result['focusedIssue'] as Json?,
                (widget.result['keepouts'] as List? ?? []).cast<Json>(),
              ),
              child: const SizedBox.expand(),
            ),
          ),
        ),
        if (motions.isNotEmpty)
          Slider(
            value: selected.toDouble(),
            max: math.max(1, motions.length - 1).toDouble(),
            divisions: math.max(1, motions.length - 1),
            onChanged: (v) => setState(() => selected = v.round()),
          ),
        Padding(
          padding: const EdgeInsets.all(12),
          child: SelectableText(
            motions.isEmpty
                ? 'No motion in this preview. Review the drawing, keepouts and findings.'
                : 'Motion ${selected + 1}/${motions.length}: ${motion['kind']}  X ${motion['x']}  Y ${motion['y']}  Z ${motion['z']} ${widget.units}  Feed ${motion['feed'] ?? '-'} ${widget.units}/s',
          ),
        ),
      ],
    );
  }
}

class MotionPainter extends CustomPainter {
  MotionPainter(
    this.blocks,
    this.selected,
    this.geometry, [
    this.focusedIssue,
    this.keepouts = const [],
  ]);
  final List<Json> keepouts;
  final Json? focusedIssue;
  final List<List<Json>> blocks;
  final Json selected;
  final List<Json> geometry;
  @override
  void paint(Canvas canvas, Size size) {
    final points = blocks
        .expand((b) => b)
        .map((m) => Offset(number(m['x']), number(m['y'])))
        .toList();
    for (final z in keepouts) {
      points.addAll((z['points'] as List? ?? []).map(point));
    }
    for (final e in geometry) {
      switch (e['kind']) {
        case 'circle':
          final c = point(e['center']), r = number(e['radius']);
          points.addAll([c - Offset(r, r), c + Offset(r, r)]);
        case 'rectangle':
          points.addAll([point(e['corner_a']), point(e['corner_b'])]);
        case 'polyline':
          points.addAll((e['points'] as List).map(point));
        case 'line':
          points.addAll([point(e['start']), point(e['end'])]);
      }
    }
    if (focusedIssue?['point'] != null) {
      points.add(point(focusedIssue!['point']));
    }
    if (points.isEmpty) return;
    var minX = points.first.dx,
        maxX = minX,
        minY = points.first.dy,
        maxY = minY;
    for (final p in points) {
      minX = math.min(minX, p.dx);
      maxX = math.max(maxX, p.dx);
      minY = math.min(minY, p.dy);
      maxY = math.max(maxY, p.dy);
    }
    for (final b in blocks) {
      for (final m in b) {
        final p = Offset(number(m['x']), number(m['y']));
        minX = math.min(minX, p.dx);
        maxX = math.max(maxX, p.dx);
        minY = math.min(minY, p.dy);
        maxY = math.max(maxY, p.dy);
        if (m['center'] != null) {
          final c = point(m['center']), r = (p - c).distance;
          minX = math.min(minX, c.dx - r);
          maxX = math.max(maxX, c.dx + r);
          minY = math.min(minY, c.dy - r);
          maxY = math.max(maxY, c.dy + r);
        }
      }
    }
    final scale = math.min(
      (size.width - 40) / math.max(.001, maxX - minX),
      (size.height - 40) / math.max(.001, maxY - minY),
    );
    Offset transform(Offset p) => Offset(
      size.width / 2 + (p.dx - (minX + maxX) / 2) * scale,
      size.height / 2 - (p.dy - (minY + maxY) / 2) * scale,
    );
    paintKeepouts(canvas, keepouts, transform);
    final outline = Paint()
      ..color = Colors.grey
      ..strokeWidth = 1
      ..style = PaintingStyle.stroke;
    for (final e in geometry) {
      final highlighted = (focusedIssue?['entity_ids'] as List? ?? []).contains(
        e['id'],
      );
      outline.color = highlighted ? Colors.pinkAccent : Colors.grey;
      outline.strokeWidth = highlighted ? 3 : 1;
      switch (e['kind']) {
        case 'circle':
          canvas.drawCircle(
            transform(point(e['center'])),
            number(e['radius']) * scale,
            outline,
          );
        case 'rectangle':
          canvas.drawRect(
            Rect.fromPoints(
              transform(point(e['corner_a'])),
              transform(point(e['corner_b'])),
            ),
            outline,
          );
        case 'line':
          canvas.drawLine(
            transform(point(e['start'])),
            transform(point(e['end'])),
            outline,
          );
        case 'polyline':
          final vertices = (e['points'] as List)
              .map((p) => transform(point(p)))
              .toList();
          if (vertices.isNotEmpty) {
            final path = Path()..moveTo(vertices.first.dx, vertices.first.dy);
            for (final p in vertices.skip(1)) {
              path.lineTo(p.dx, p.dy);
            }
            if (e['closed'] == true) {
              path.close();
            }
            canvas.drawPath(path, outline);
          }
      }
    }
    Offset? previous;
    for (final block in blocks) {
      for (final m in block) {
        final p = Offset(number(m['x']), number(m['y']));
        final paint = Paint()
          ..color = (m['kind'] == 'rapid' ? Colors.orange : Colors.teal)
          ..strokeWidth = 1.5
          ..style = PaintingStyle.stroke;
        if (previous != null) {
          if (m['center'] != null &&
              ['arc_cw', 'arc_ccw'].contains(m['kind'])) {
            final c = point(m['center']), r = (previous - c).distance;
            final start = math.atan2(previous.dy - c.dy, previous.dx - c.dx),
                end = math.atan2(p.dy - c.dy, p.dx - c.dx);
            var sweep = end - start;
            if (m['kind'] == 'arc_cw') {
              while (sweep >= -1e-10) {
                sweep -= 2 * math.pi;
              }
            } else {
              while (sweep <= 1e-10) {
                sweep += 2 * math.pi;
              }
            }
            canvas.drawArc(
              Rect.fromCircle(center: transform(c), radius: r * scale),
              -start,
              -sweep,
              false,
              paint,
            );
          } else {
            canvas.drawLine(transform(previous), transform(p), paint);
          }
        }
        previous = p;
      }
    }
    if (focusedIssue?['point'] != null) {
      canvas.drawCircle(
        transform(point(focusedIssue!['point'])),
        8,
        Paint()
          ..color = Colors.pinkAccent
          ..style = PaintingStyle.stroke
          ..strokeWidth = 3,
      );
    }
    if (selected.isNotEmpty) {
      canvas.drawCircle(
        transform(Offset(number(selected['x']), number(selected['y']))),
        5,
        Paint()..color = Colors.pinkAccent,
      );
    }
  }

  @override
  bool shouldRepaint(covariant MotionPainter oldDelegate) => true;
}
