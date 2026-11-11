import 'mailer_dialog.dart';
import 'svg_import_dialog.dart';
import 'dart:async';
import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'controller.dart';
import 'canvas.dart';
import 'point_editor.dart';
import 'reload_ui.dart';
import 'layers_dialog.dart';
import 'footer.dart';
import 'shape_tools_dialog.dart';
import 'appearance_dialog.dart';
import 'push_fit_dialog.dart';
import 'patchwork_dialog.dart';
import 'repair_review.dart';
import 'repair_suite_dialog.dart';
import 'small_geometry_dialog.dart';
import 'shop_pdf_dialog.dart';
import 'dimension_dialog.dart';
import 'dimension_actions.dart';
import 'guides_dialog.dart';
import 'cleave_dialog.dart';
import 'geometry_inspector.dart';
import 'recovery_dialog.dart';
import 'clipboard_dialog.dart';
import 'manufacturing_workspace.dart';
import 'stock_material.dart';
import 'cutoff_dialog.dart';
import 'repeat_dialog.dart';
import 'assembly_review_dialog.dart';
import 'pan_authoring_dialog.dart';
import 'cabinet_authoring_dialog.dart';

const designTeal = Color(0xff087f75);

const shortcutLabels = <String, String>{
  'open': 'Ctrl+O',
  'new': 'Ctrl+N',
  'save': 'Ctrl+S',
  'saveAs': 'Ctrl+Shift+S',
  'print': 'Ctrl+P',
  'undo': 'Ctrl+Z',
  'redo': 'Ctrl+Y / Ctrl+Shift+Z',
  'all': 'Ctrl+A',
  'copy': 'Ctrl+C',
  'cut': 'Ctrl+X',
  'paste': 'Ctrl+V',
  'delete': 'Delete',
  'clear': 'Esc',
};

class DesignCommand {
  const DesignCommand(
    this.id,
    this.label,
    this.icon, {
    this.selection = false,
    this.pending = false,
  });
  final String id, label;
  final IconData icon;
  final bool selection, pending;
}

const manufacturingGroups = <String, List<DesignCommand>>{
  'File': [
    DesignCommand('open', 'Open drawing', Icons.folder_outlined),
    DesignCommand('save', 'Save', Icons.save_outlined),
    DesignCommand('saveAs', 'Save as', Icons.save_as_outlined),
    DesignCommand('new', 'New drawing', Icons.note_add_outlined),
  ],
  'Setup': [
    DesignCommand('cam.stock', 'Job setup', Icons.settings_outlined),
    DesignCommand(
      'cam.readiness',
      'Guided readiness',
      Icons.fact_check_outlined,
    ),
    DesignCommand('cam.library', 'Tool library', Icons.menu_book_outlined),
    DesignCommand('cam.turret', 'Turret setup', Icons.apps),
    DesignCommand('cam.keepouts', 'Fixtures / keepouts', Icons.fence),
  ],
  'Create': [
    DesignCommand('cam.outside', 'Outside profile', Icons.crop_square),
    DesignCommand('cam.inside', 'Inside profile', Icons.filter_center_focus),
    DesignCommand('cam.line', 'Cut Line', Icons.horizontal_rule),
    DesignCommand('cam.fill', 'Cleanout', Icons.layers_outlined),
    DesignCommand('cam.drill', 'Drill', Icons.control_point),
    DesignCommand('cam.engrave', 'Engrave', Icons.edit_outlined),
    DesignCommand('cam.special', 'More machining tools', Icons.more_horiz),
    DesignCommand('repeat', 'Step and Repeat', Icons.grid_view),
    DesignCommand('cutoff', 'Sheet Cutoff Lines', Icons.content_cut),
  ],
  'Efficiency': [
    DesignCommand(
      'cam.optimization',
      'Compare cycle optimization',
      Icons.speed,
    ),
  ],
  'Inspect': [
    DesignCommand('panReview', 'DR-pan machining features', Icons.view_in_ar),
    DesignCommand(
      'cabinetReview',
      'Cabinet Tabs registration',
      Icons.view_in_ar_outlined,
    ),
    DesignCommand('cam.pick', 'Graphical Manufacturing tools', Icons.ads_click),
    DesignCommand(
      'cam.inspect',
      'Operations & review',
      Icons.fact_check_outlined,
    ),
  ],
  'Output': [
    DesignCommand('cam.generate', 'Generate toolpaths', Icons.play_arrow),
    DesignCommand('cam.output', 'Review and export', Icons.output),
  ],
};

const designGroups = <String, List<DesignCommand>>{
  'File': [
    DesignCommand('open', 'Open', Icons.folder_open),
    DesignCommand('save', 'Save', Icons.save_outlined),
    DesignCommand('saveAs', 'Save As', Icons.save_as_outlined),
    DesignCommand('new', 'New', Icons.note_add_outlined),
    DesignCommand('import', 'Import into job', Icons.file_open_outlined),
    DesignCommand('openSvg', 'Open SVG in new tab...', Icons.note_add_outlined),
    DesignCommand('importSvg', 'Import SVG...', Icons.file_upload_outlined),
    DesignCommand('export', 'Export DXF', Icons.output),
    DesignCommand('print', 'Shop drawing PDF', Icons.print_outlined),
    DesignCommand('recovery', 'Recover drawing…', Icons.restore_page_outlined),
  ],
  'Create': [
    DesignCommand('line', 'Line', Icons.show_chart),
    DesignCommand('rectangle', 'Rectangle', Icons.crop_square),
    DesignCommand('circle', 'Circle', Icons.circle_outlined),
    DesignCommand('text', 'Text', Icons.text_fields),
    DesignCommand('ellipse', 'Ellipse', Icons.panorama_wide_angle_outlined),
    DesignCommand('slot', 'Slot', Icons.rounded_corner),
    DesignCommand('polygon', 'Polygon', Icons.hexagon_outlined),
    DesignCommand('arrow', 'Arrow', Icons.east),
    DesignCommand('double_arrow', 'Double arrow', Icons.sync_alt),
    DesignCommand('chevron', 'Chevron', Icons.chevron_right),
    DesignCommand('star', 'Star', Icons.star_border),
    DesignCommand('precise', 'Draw by dimensions', Icons.straighten),
    DesignCommand('dimension', 'Dimension: pick points', Icons.square_foot),
    DesignCommand('dimensions', 'Shop dimensions / notes', Icons.architecture),
  ],
  'Modify': [
    DesignCommand('move', 'Move / rotate', Icons.open_with, selection: true),
    DesignCommand('scale', 'Scale', Icons.aspect_ratio, selection: true),
    DesignCommand('mirrorX', 'Mirror X', Icons.flip, selection: true),
    DesignCommand('points', 'Edit points', Icons.edit_outlined),
    DesignCommand(
      'mirrorY',
      'Mirror Y',
      Icons.flip_camera_android_outlined,
      selection: true,
    ),
    DesignCommand('duplicate', 'Duplicate', Icons.copy_all, selection: true),
    DesignCommand('delete', 'Delete', Icons.delete_outline, selection: true),
    DesignCommand(
      'appearance',
      'Object Fill / Stroke',
      Icons.palette_outlined,
      selection: true,
    ),
    DesignCommand(
      'pointCoordinates',
      'Point coordinates',
      Icons.table_rows_outlined,
      selection: true,
    ),
    DesignCommand('merge', 'Join lines', Icons.join_inner, selection: true),
    DesignCommand(
      'cleave',
      'Cleave / Split along guide',
      Icons.call_split,
      selection: true,
    ),
    DesignCommand(
      'cleaveLine',
      'Cleave / Split: draw line',
      Icons.gesture,
      selection: true,
    ),
    DesignCommand(
      'joinRepair',
      'Join / repair contours',
      Icons.healing,
      selection: true,
    ),
    DesignCommand(
      'backtracks',
      'Remove micro backtracks',
      Icons.undo,
      selection: true,
    ),
    DesignCommand(
      'nearPoints',
      'Remove near-duplicate points',
      Icons.scatter_plot_outlined,
      selection: true,
    ),
    DesignCommand(
      'explode',
      'Explode contour',
      Icons.call_split,
      selection: true,
    ),
    DesignCommand(
      'resizeCircles',
      'Resize circles',
      Icons.radio_button_unchecked,
      selection: true,
    ),
    DesignCommand(
      'textCurves',
      'Text to curves',
      Icons.font_download_outlined,
      selection: true,
    ),
    DesignCommand(
      'outline',
      'Offset outline',
      Icons.layers_outlined,
      selection: true,
    ),
    DesignCommand(
      'convertShape',
      'Convert to shape',
      Icons.auto_fix_high,
      selection: true,
    ),
    DesignCommand(
      'editableOutline',
      'Editable outlines',
      Icons.polyline_outlined,
      selection: true,
    ),
  ],
  'Arrange': [
    DesignCommand(
      'align_left',
      'Align left',
      Icons.align_horizontal_left,
      selection: true,
    ),
    DesignCommand(
      'align_horizontal_center',
      'Center horizontally',
      Icons.align_horizontal_center,
      selection: true,
    ),
    DesignCommand(
      'align_bottom',
      'Align bottom',
      Icons.align_vertical_bottom,
      selection: true,
    ),
    DesignCommand(
      'space_horizontal_equal',
      'Space horizontally',
      Icons.horizontal_distribute,
      selection: true,
    ),
    DesignCommand(
      'align_right',
      'Align right',
      Icons.align_horizontal_right,
      selection: true,
    ),
    DesignCommand(
      'align_top',
      'Align top',
      Icons.align_vertical_top,
      selection: true,
    ),
    DesignCommand(
      'align_vertical_center',
      'Center vertically',
      Icons.align_vertical_center,
      selection: true,
    ),
    DesignCommand(
      'align_both_centers',
      'Center both',
      Icons.filter_center_focus,
      selection: true,
    ),
    DesignCommand(
      'space_vertical_equal',
      'Space vertically',
      Icons.vertical_distribute,
      selection: true,
    ),
    DesignCommand('group', 'Group', Icons.group_work_outlined, selection: true),
    DesignCommand(
      'space_horizontal_gap',
      'Exact horizontal gap',
      Icons.horizontal_distribute,
      selection: true,
    ),
    DesignCommand(
      'space_vertical_gap',
      'Exact vertical gap',
      Icons.vertical_distribute,
      selection: true,
    ),
    DesignCommand(
      'ungroup',
      'Ungroup',
      Icons.scatter_plot_outlined,
      selection: true,
    ),
  ],
  'Macros': [
    DesignCommand('union', 'Union', Icons.join_full, selection: true),
    DesignCommand(
      'subtract_front',
      'Subtract front',
      Icons.join_left,
      selection: true,
    ),
    DesignCommand('intersect', 'Intersect', Icons.join_inner, selection: true),
    DesignCommand(
      'cabinetAuthor',
      'Cabinet Tabs authoring',
      Icons.dashboard_customize,
    ),
    DesignCommand('panAuthor', 'DR Pans', Icons.view_in_ar),
    DesignCommand(
      'mailerAuthor',
      'Hinged-mailer box',
      Icons.inventory_2_outlined,
    ),
    DesignCommand('repeat', 'Step and Repeat', Icons.grid_view),
    DesignCommand('cutoff', 'Sheet Cutoff Lines', Icons.content_cut),
    DesignCommand(
      'subtract_back',
      'Subtract back',
      Icons.join_right,
      selection: true,
    ),
    DesignCommand(
      'exclude',
      'Exclude overlap',
      Icons.join_full_outlined,
      selection: true,
    ),
    DesignCommand('compound', 'Compound', Icons.link, selection: true),
    DesignCommand('uncompound', 'Uncompound', Icons.link_off, selection: true),
    DesignCommand('nest', 'Auto nest', Icons.dashboard_customize_outlined),
    DesignCommand('patchwork', 'Patchwork', Icons.grid_view),
    DesignCommand('pushFit', 'Push-through fit', Icons.extension_outlined),
  ],
  'Inspect': [
    DesignCommand('panReview', 'DR-pan machining features', Icons.view_in_ar),
    DesignCommand(
      'cabinetReview',
      'Cabinet Tabs registration',
      Icons.view_in_ar_outlined,
    ),
    DesignCommand('measure', 'Measure', Icons.straighten),
    DesignCommand('fit', 'Fit drawing', Icons.fit_screen),
    DesignCommand('plate', 'Fit plate', Icons.crop_landscape),
    DesignCommand('circles', 'Select circles', Icons.control_point),
    DesignCommand('all', 'Select all', Icons.select_all),
    DesignCommand('invert', 'Invert selection', Icons.flip_to_back),
    DesignCommand('clear', 'Clear selection', Icons.deselect),
    DesignCommand('properties', 'Properties', Icons.tune),
    DesignCommand(
      'repairSuite',
      'Run Repair Suite',
      Icons.build_circle_outlined,
    ),
    DesignCommand('checkContours', 'Check contours', Icons.fact_check_outlined),
    DesignCommand('smallGeometry', 'Small geometry / Specks', Icons.search),
    DesignCommand('guides', 'Construction guides', Icons.grid_4x4),
  ],
};

class DesignApp extends StatefulWidget {
  const DesignApp({
    super.key,
    this.controller,
    this.startBackend = true,
    this.controllerFactory,
  });
  final DesignController? controller;
  final DesignController Function()? controllerFactory;
  final bool startBackend;
  @override
  State<DesignApp> createState() => _DesignAppState();
}

class _DesignAppState extends State<DesignApp> {
  late final DesignController model =
      widget.controller ?? DesignController(recoveryEnabled: true);
  bool dark = false;
  @override
  void dispose() {
    if (widget.controller == null) model.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
    debugShowCheckedModeBanner: false,
    title: 'CAM Workbench · Design',
    theme: ThemeData(
      useMaterial3: true,
      brightness: dark ? Brightness.dark : Brightness.light,
      colorScheme: ColorScheme.fromSeed(
        seedColor: designTeal,
        brightness: dark ? Brightness.dark : Brightness.light,
      ),
      fontFamily: 'Segoe UI',
      visualDensity: VisualDensity.compact,
      materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(
          minimumSize: const Size(32, 32),
          maximumSize: const Size(40, 32),
          padding: const EdgeInsets.all(4),
          tapTargetSize: MaterialTapTargetSize.shrinkWrap,
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        isDense: true,
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(6)),
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 10,
          vertical: 12,
        ),
      ),
      textTheme: const TextTheme(
        bodyMedium: TextStyle(fontSize: 12),
        bodySmall: TextStyle(fontSize: 11),
      ),
    ),
    home: DocumentTabs(
      initial: model,
      factory:
          widget.controllerFactory ??
          () => DesignController(recoveryEnabled: true),
      startBackend: widget.startBackend,
      dark: dark,
      onTheme: (v) => setState(() => dark = v),
    ),
  );
}

class DocumentTabs extends StatefulWidget {
  const DocumentTabs({
    super.key,
    required this.initial,
    required this.factory,
    required this.startBackend,
    required this.dark,
    required this.onTheme,
  });
  final DesignController initial;
  final DesignController Function() factory;
  final bool startBackend, dark;
  final ValueChanged<bool> onTheme;
  @override
  State<DocumentTabs> createState() => _DocumentTabsState();
}

class _DocumentTabsState extends State<DocumentTabs> {
  static const platform = MethodChannel('workbench/design');
  final sharedClipboard = DrawingClipboard();
  final models = <DesignController>[];
  final keys = <GlobalKey<_DesignWorkspaceState>>[];
  int active = 0;
  bool working = false;
  @override
  void initState() {
    super.initState();
    sharedClipboard.addListener(changed);
    add(widget.initial);
    platform.setMethodCallHandler((call) async {
      if (call.method == 'requestClose') await closeWindow();
    });
  }

  void add(DesignController model) {
    model.drawingClipboard = sharedClipboard;
    models.add(model);
    keys.add(GlobalKey<_DesignWorkspaceState>());
    model.addListener(changed);
  }

  void changed() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) setState(() {});
    });
    WidgetsBinding.instance.ensureVisualUpdate();
  }

  Future<void> openDocument(String command, Json params) async {
    if (working) return;
    if (command == 'svg.open') {
      await openSvgDocument(params);
      return;
    }
    if (command == 'project.open') {
      String normalize(dynamic p) => '$p'.replaceAll('\\', '/').toLowerCase();
      final match = models.indexWhere(
        (m) => [m.state['path'], m.state['savedPath']].whereType<String>().any(
          (p) => normalize(p) == normalize(params['path']),
        ),
      );
      if (match >= 0) {
        setState(() => active = match);
        return;
      }
    }
    setState(() => working = true);
    final model = widget.factory();
    try {
      await model.start();
      if (!model.ready || !await model.run(command, params)) {
        models[active].message = model.message;
        await model.shutdown();
        model.dispose();
        return;
      }
      if (!mounted) {
        await model.shutdown();
        model.dispose();
        return;
      }
      setState(() {
        add(model);
        active = models.length - 1;
      });
      await WidgetsBinding.instance.endOfFrame;
      final workspace = keys[active].currentState;
      workspace?.view.fit(
        bounds(model.state['bounds']) ?? const Rect.fromLTWH(0, 0, 96, 48),
      );
      workspace?.workspaceFocus.requestFocus();
      if (command == 'project.open' && mounted) {
        await reviewOpeningRepair(context, model);
      }
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  Future<void> openSvgDocument(Json params) async {
    if (working) return;
    final origin = models[active];
    setState(() => working = true);
    DesignController? model;
    bool published = false, applied = false;
    try {
      final path = params['path'] as String;
      final filename = path.replaceAll('\\', '/').split('/').last;
      final name = filename.replaceFirst(
        RegExp(r'\.svg$', caseSensitive: false),
        '',
      );
      model = widget.factory();
      await model.start();
      if (!model.ready) throw StateError(model.message);
      if (model.state['svgImport'] != true) {
        throw StateError(
          'Restart the development launcher to load SVG import.',
        );
      }
      if (!await model.run('project.new', {
        'name': name,
        'units': params['units'] ?? origin.units,
      })) {
        throw StateError(model.message);
      }
      if (!mounted) return;
      applied =
          await showDialog<bool>(
            context: context,
            barrierDismissible: false,
            builder: (_) =>
                SvgImportDialog(model: model!, path: path, newDocument: true),
          ) ==
          true;
      if (!applied || !mounted) return;
      final destination = model;
      setState(() {
        add(destination);
        active = models.length - 1;
        published = true;
      });
      final key = keys.last;
      await WidgetsBinding.instance.endOfFrame;
      key.currentState?.view.fit(
        bounds(destination.state['bounds']) ??
            const Rect.fromLTWH(0, 0, 96, 48),
      );
      key.currentState?.workspaceFocus.requestFocus();
      if (mounted) await reviewOpeningRepair(context, destination);
    } catch (e) {
      (published ? model! : origin).message = 'Could not open SVG: $e';
    } finally {
      if (model != null && !published) {
        try {
          // A canceled private draft has no adopted work. Applied work keeps
          // recovery if the window disappears before its tab can be published.
          if (!applied && model.ready && !await clearRecovery(model)) {
            origin.message =
                'SVG review closed; its temporary recovery could not be cleared: ${model.message}';
          }
        } catch (e) {
          origin.message =
              'SVG review closed; temporary recovery cleanup failed: $e';
        } finally {
          try {
            await model.shutdown();
          } catch (e) {
            origin.message =
                'SVG review closed; its private engine did not shut down cleanly: $e';
          } finally {
            model.dispose();
          }
        }
      }
      if (mounted) setState(() => working = false);
    }
  }

  Future<bool> clearRecovery(DesignController m) async =>
      m.state['recovery']?['enabled'] != true || await m.run('recovery.clear');

  Future<void> closeTab(int index) async {
    if (working) return;
    if (models.length == 1) {
      await closeWindow();
      return;
    }
    setState(() {
      working = true;
      active = index;
    });
    try {
      if (!await keys[index].currentState!.confirmDiscard()) return;
      final m = models[index];
      if (!await clearRecovery(m)) return;
      await m.shutdown();
      m.removeListener(changed);
      setState(() {
        models.removeAt(index);
        keys.removeAt(index);
        active = math.min(index, models.length - 1);
      });
      // Workspace listeners must be removed before disposing its controller.
      await WidgetsBinding.instance.endOfFrame;
      if (m != widget.initial) m.dispose();
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  Future<void> closeWindow() async {
    if (working) return;
    setState(() => working = true);
    try {
      // Gather every decision first. A later Cancel leaves every tab/recovery open.
      for (var i = 0; i < models.length; i++) {
        setState(() => active = i);
        if (!await keys[i].currentState!.confirmDiscard()) return;
      }
      for (final m in models) {
        if (!await clearRecovery(m)) {
          for (final pending in models.where((item) => item.dirty)) {
            await pending.run('recovery.checkpoint');
          }
          return;
        }
      }
      for (final m in models) {
        await m.shutdown();
      }
      await platform.invokeMethod('close');
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  @override
  void dispose() {
    platform.setMethodCallHandler(null);
    sharedClipboard.removeListener(changed);
    sharedClipboard.dispose();
    for (final m in models) {
      m.removeListener(changed);
      if (m != widget.initial) m.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Column(
    children: [
      Material(
        color: Theme.of(context).colorScheme.surfaceContainer,
        child: SizedBox(
          height: 32,
          child: Row(
            children: [
              Expanded(
                child: SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: Row(
                    children: [
                      for (var i = 0; i < models.length; i++)
                        Container(
                          key: ValueKey(keys[i]),
                          color: i == active
                              ? Theme.of(context).colorScheme.secondaryContainer
                              : null,
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              TextButton(
                                onPressed: working
                                    ? null
                                    : () => setState(() => active = i),
                                child: Text(
                                  '${models[i].state['name'] ?? 'Untitled'}${models[i].dirty ? ' •' : ''}',
                                ),
                              ),
                              IconButton(
                                tooltip: 'Close drawing ${i + 1}',
                                iconSize: 16,
                                onPressed: working ? null : () => closeTab(i),
                                icon: const Icon(Icons.close),
                              ),
                            ],
                          ),
                        ),
                    ],
                  ),
                ),
              ),
              IconButton(
                tooltip: 'New drawing tab',
                onPressed: working
                    ? null
                    : () => keys[active].currentState?.action('new'),
                icon: const Icon(Icons.add),
              ),
              if (working)
                const SizedBox(
                  width: 20,
                  height: 20,
                  child: Icon(Icons.hourglass_top, size: 16),
                ),
            ],
          ),
        ),
      ),
      Expanded(
        child: IndexedStack(
          index: active,
          children: [
            for (var i = 0; i < models.length; i++)
              ExcludeFocus(
                excluding: active != i,
                child: TickerMode(
                  enabled: active == i,
                  child: DesignWorkspace(
                    key: keys[i],
                    model: models[i],
                    startBackend: i == 0 && widget.startBackend,
                    dark: widget.dark,
                    onTheme: widget.onTheme,
                    manageWindow: false,
                    offerStartupRecovery: i == 0,
                    active: active == i,
                    onDocument: openDocument,
                  ),
                ),
              ),
          ],
        ),
      ),
    ],
  );
}

class DesignWorkspace extends StatefulWidget {
  const DesignWorkspace({
    super.key,
    required this.model,
    required this.startBackend,
    required this.dark,
    required this.onTheme,
    this.onDocument,
    this.manageWindow = true,
    this.offerStartupRecovery = true,
    this.active = true,
  });
  final DesignController model;
  final bool startBackend, dark, manageWindow, offerStartupRecovery, active;
  final Future<void> Function(String, Json)? onDocument;
  final ValueChanged<bool> onTheme;
  @override
  State<DesignWorkspace> createState() => _DesignWorkspaceState();
}

class _DesignWorkspaceState extends State<DesignWorkspace> {
  static const platform = MethodChannel('workbench/design');
  final view = DesignViewport();
  late final PointEditor pointEditor;
  final workspaceFocus = FocusNode(debugLabel: 'Design shortcuts');
  final stackKey = GlobalKey();
  DesignController get m => widget.model;
  bool manufacturing = false;
  Map<String, VoidCallback?> manufacturingActions = {};
  Map<String, List<DesignCommand>> get groups =>
      manufacturing ? manufacturingGroups : designGroups;
  List<String> designOrder = designGroups.keys.toList();
  List<String> manufacturingOrder = manufacturingGroups.keys.toList();
  List<String> get order => manufacturing ? manufacturingOrder : designOrder;
  set order(List<String> value) {
    if (manufacturing) {
      manufacturingOrder = value;
    } else {
      designOrder = value;
    }
  }

  final designFloating = <String, Offset>{};
  final manufacturingFloating = <String, Offset>{};
  Map<String, Offset> get floating =>
      manufacturing ? manufacturingFloating : designFloating;

  void switchWorkspace(bool next) {
    if (next == manufacturing || !m.ready || m.busy || pointEditor.active) {
      return;
    }
    final selectedDimension = view.selectedDimensionId;
    view.setTool('select');
    view.selectedDimensionId = selectedDimension;
    if (next) {
      view.hint =
          'Select contours · Shift adds to selection · middle-drag pans';
    }
    view.cursorInside = false;
    view.hoveredIds = [];
    setState(() => manufacturing = next);
    workspaceFocus.requestFocus();
  }

  Widget manufacturingShell(Map<String, VoidCallback?> actions, Widget canvas) {
    manufacturingActions = actions;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        menus(),
        ribbon(),
        stockStrip(),
        ...documentNotices(),
        Expanded(
          child: LayoutBuilder(
            builder: (context, c) => Stack(
              key: stackKey,
              children: [
                Positioned.fill(child: canvas),
                for (final entry in floating.entries)
                  Positioned(
                    left: entry.value.dx.clamp(
                      0,
                      math.max(0, c.maxWidth - 300),
                    ),
                    top: entry.value.dy.clamp(
                      0,
                      math.max(0, c.maxHeight - 180),
                    ),
                    child: SizedBox(
                      width: 300,
                      child: floatingPanel(entry.key),
                    ),
                  ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  String layer = '0';
  Json? get activePlate =>
      m.plates
          .where((p) => p['number'] == m.state['activePlate'])
          .firstOrNull ??
      m.plates.firstOrNull;
  bool inspector = true, showLayers = true, loaded = false, closing = false;
  Timer? saveLayoutTimer;
  Color get panel => widget.dark ? const Color(0xff203237) : Colors.white;
  @override
  void initState() {
    super.initState();
    pointEditor = PointEditor(m)..addListener(pointsChanged);
    view.addListener(toolChanged);
    m.addListener(changed);
    if (widget.startBackend) {
      m.start();
    } else {
      loaded = true;
    }
    if (widget.manageWindow) {
      platform.setMethodCallHandler((call) async {
        if (call.method == 'requestClose' && !closing) {
          closing = true;
          if (await confirmDiscard()) {
            if (m.state['recovery']?['enabled'] == true &&
                !await m.run('recovery.clear')) {
              closing = false;
              return;
            }
            await m.shutdown();
            await platform.invokeMethod('close');
          }
          closing = false;
        }
      });
    }
  }

  @override
  void didUpdateWidget(covariant DesignWorkspace oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.active && !oldWidget.active) workspaceFocus.requestFocus();
  }

  void pointsChanged() {
    if (!mounted) return;
    if (pointEditor.active) {
      view.hint = pointEditor.hint;
      view.changed();
    }
    setState(() {});
  }

  void toolChanged() {
    if (view.tool != 'points' && pointEditor.active) pointEditor.finish();
  }

  void escape() {
    if (manufacturing) {
      m.select({});
      return;
    }
    view.snapTarget = null;
    view.changed();
    if (pointEditor.active) {
      if (pointEditor.dragging) {
        pointEditor.cancelDrag();
        return;
      }
      if (pointEditor.inserting) {
        pointEditor.toggleInsert();
        return;
      }
      view.setTool('select');
      return;
    }
    view.setTool('select');
    m.select({});
  }

  void nudge(double dx, double dy) {
    if (manufacturing) return;
    if (pointEditor.active) {
      pointEditor.edit('move', {'dx': dx, 'dy': dy});
    } else {
      m.run('geometry.transform', {'dx': dx, 'dy': dy});
    }
  }

  void changed() {
    if (!mounted) return;
    if (m.ready && layer != '0' && !m.layers.any((l) => l['name'] == layer)) {
      layer = '0';
    }
    if (m.ready && !loaded) {
      loaded = true;
      final pref = m.preferences;
      final saved = (pref['order'] as List? ?? [])
          .whereType<String>()
          .where(designGroups.containsKey)
          .toSet()
          .toList();
      order = [...saved, ...designGroups.keys.where((g) => !saved.contains(g))];
      if (pref['floating'] is Map) {
        for (final e in (pref['floating'] as Map).entries) {
          if (designGroups.containsKey(e.key) &&
              e.value is List &&
              e.value.length == 2) {
            floating[e.key] = Offset(number(e.value[0]), number(e.value[1]));
          }
        }
      }
      final camOrder = (pref['manufacturingOrder'] as List? ?? [])
          .whereType<String>()
          .where(manufacturingGroups.containsKey)
          .toSet()
          .toList();
      manufacturingOrder = [
        ...camOrder,
        ...manufacturingGroups.keys.where((g) => !camOrder.contains(g)),
      ];
      for (final e in ((pref['manufacturingFloating'] as Map?) ?? {}).entries) {
        if (manufacturingGroups.containsKey(e.key) &&
            e.value is List &&
            e.value.length == 2) {
          manufacturingFloating[e.key] = Offset(
            number(e.value[0]),
            number(e.value[1]),
          );
        }
      }
      view.grid = pref['grid'] != false;
      view.snap = pref['snap'] == true;
      inspector = pref['inspector'] != false;
      showLayers = pref['layers'] != false;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) {
          widget.onTheme(pref['dark'] == true);
          fitPlate();
          if (widget.offerStartupRecovery &&
              (m.state['recovery']?['candidates'] as List? ?? []).isNotEmpty) {
            offerRecovery();
          }
        }
      });
    }
    setState(() {});
  }

  @override
  void dispose() {
    saveLayoutTimer?.cancel();
    m.removeListener(changed);
    pointEditor.removeListener(pointsChanged);
    pointEditor.dispose();
    view.removeListener(toolChanged);
    view.dispose();
    workspaceFocus.dispose();
    if (widget.manageWindow) platform.setMethodCallHandler(null);
    super.dispose();
  }

  void saveLayout() {
    saveLayoutTimer?.cancel();
    saveLayoutTimer = Timer(const Duration(milliseconds: 350), () {
      m.savePreferences({
        ...m.preferences,
        'order': designOrder,
        'floating': designFloating.map((k, v) => MapEntry(k, [v.dx, v.dy])),
        'manufacturingOrder': manufacturingOrder,
        'manufacturingFloating': manufacturingFloating.map(
          (k, v) => MapEntry(k, [v.dx, v.dy]),
        ),
        'grid': view.grid,
        'snap': view.snap,
        'dark': widget.dark,
        'inspector': inspector,
        'layers': showLayers,
      });
    });
  }

  void finishPaletteDrag(String group, DraggableDetails details) {
    if (details.wasAccepted) return;
    final box = stackKey.currentContext?.findRenderObject() as RenderBox?;
    if (box == null) return;
    setState(() => floating[group] = box.globalToLocal(details.offset));
    saveLayout();
  }

  Widget paletteDragPreview(String group) => Material(
    elevation: 8,
    color: panel,
    borderRadius: BorderRadius.circular(6),
    child: SizedBox(
      width: 300,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Text('$group · drop on the toolbar to dock'),
      ),
    ),
  );

  void fitPlate() {
    final p =
        m.plates
            .where((p) => p['number'] == m.state['activePlate'])
            .firstOrNull ??
        m.plates.firstOrNull;
    if (p != null) {
      view.fit(
        Rect.fromLTWH(
          number(p['x']),
          number(p['y']),
          number(p['width']),
          number(p['height']),
        ),
      );
    }
  }

  Future<void> offerRecovery() async {
    if (await showRecoveryDialog(context, m) && mounted) {
      layer = '0';
      view.setTool('select');
      view.fit(bounds(m.state['bounds']) ?? const Rect.fromLTWH(0, 0, 96, 48));
    }
    if (mounted) workspaceFocus.requestFocus();
  }

  Future<bool> confirmDiscard() async {
    if (m.busy ||
        m.pending.isNotEmpty ||
        ModalRoute.of(context)?.isCurrent == false ||
        pointEditor.dragging ||
        view.start != null ||
        view.dimensionPreview != null) {
      m.message =
          'Wait for the current action to finish before closing this drawing.';
      setState(() {});
      return false;
    }
    if (!m.dirty) return true;
    final result = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Save this drawing?'),
        content: Text('Save changes to ${m.state['name'] ?? 'Untitled'}?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, 'cancel'),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, 'discard'),
            child: const Text('Discard'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, 'save'),
            child: const Text('Save As'),
          ),
        ],
      ),
    );
    if (result == 'save') return save(asNew: true);
    return result == 'discard';
  }

  Future<String?> pick(String mode) async {
    try {
      return await platform.invokeMethod<String>(mode);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('File dialog unavailable: $e')));
      }
      return null;
    }
  }

  Future<bool> save({bool asNew = false}) async {
    final path = asNew ? null : m.state['savedPath'] as String?;
    final chosen = path ?? await pick('save');
    if (chosen == null) return false;
    return m.run('project.save', {'path': chosen});
  }

  Future<Map<String, String>?> form(
    String title,
    Map<String, String> fields, {
    String? note,
  }) async {
    final controllers = fields.map(
      (key, value) => MapEntry(key, TextEditingController(text: value)),
    );
    final result = await showDialog<Map<String, String>>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(title),
        content: SizedBox(
          width: 360,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (note != null)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 14),
                    child: Text(note, style: const TextStyle(fontSize: 12)),
                  ),
                for (final entry in controllers.entries)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: TextField(
                      controller: entry.value,
                      autofocus: entry.key == fields.keys.first,
                      maxLines: entry.key == 'Points (x,y per line)' ? 8 : 1,
                      decoration: InputDecoration(labelText: entry.key),
                    ),
                  ),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(
              context,
              controllers.map((k, v) => MapEntry(k, v.text)),
            ),
            child: const Text('Apply'),
          ),
        ],
      ),
    );
    // Dialog fields can remain mounted during their exit animation.
    Future<void>.delayed(const Duration(milliseconds: 400), () {
      for (final c in controllers.values) {
        c.dispose();
      }
    });
    return result;
  }

  Future<void> numeric(
    String title,
    String command,
    Map<String, String> fields, {
    Json extra = const {},
    String? note,
  }) async {
    final result = await form(title, fields, note: note);
    if (result == null) return;
    final data = <String, dynamic>{...extra};
    for (final e in result.entries) {
      final v = double.tryParse(e.value);
      if (v == null || !v.isFinite) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('${e.key}: enter a finite number.')),
          );
        }
        return;
      }
      data[e.key] = v;
    }
    await m.run(command, data);
  }

  bool enabled(DesignCommand c) {
    if (c.id.startsWith('cam.')) {
      return m.ready &&
          !m.busy &&
          manufacturingActions[c.id.substring(4)] != null;
    }
    if (c.pending || m.busy || !m.ready) return false;
    if (c.id == 'openSvg') {
      return widget.onDocument != null && m.state['svgImport'] == true;
    }
    if (c.id == 'cabinetAuthor') return m.state['cabinetAuthoring'] == true;
    if (c.id == 'mailerAuthor') {
      return m.state['mailerAuthoring'] == true && !m.readOnly;
    }
    if (c.id == 'panAuthor') {
      return m.state['panAuthoring'] == true &&
          !m.readOnly &&
          m.selection.isNotEmpty;
    }
    if (c.id == 'panReview' || c.id == 'cabinetReview') {
      return m.state['assemblyReview'] == true;
    }
    if (c.id == 'repairSuite') {
      return m.state['repairSuite'] == true && !m.readOnly;
    }
    if (c.id == 'repeat') {
      return m.state['stepRepeat'] == true &&
          !m.readOnly &&
          m.selection.isNotEmpty;
    }
    if (c.id == 'cutoff') {
      return m.state['sheetCutoffs'] == true &&
          (!m.readOnly || m.state['manufacturing']?['editable'] == true);
    }
    if (c.id == 'recovery' && m.state['recovery']?['enabled'] != true) {
      return false;
    }
    if (c.id == 'delete' && view.selectedDimensionId != null) {
      return editEnabled('delete');
    }
    if (c.selection && m.selection.isEmpty) return false;
    if (m.readOnly &&
        !((m.state['manufacturing']?['editable'] == true ||
                m.state['cabinetAuthoring'] == true) &&
            {'save', 'saveAs'}.contains(c.id)) &&
        !{
          'open',
          'new',
          'measure',
          'fit',
          'plate',
          'circles',
          'all',
          'invert',
          'clear',
          'properties',
          'recovery',
          'checkContours',
          'smallGeometry',
          'dimensions',
          'guides',
        }.contains(c.id)) {
      return false;
    }
    return true;
  }

  bool editEnabled(String id) {
    if (manufacturing &&
        !{'undo', 'redo', 'all', 'clear', 'copy'}.contains(id)) {
      return false;
    }
    if (m.busy || !m.ready) return false;
    if (id == 'importSvg' && m.state['svgImport'] != true) return false;
    if (m.readOnly &&
        !{'copy', 'all', 'clear'}.contains(id) &&
        !((m.state['manufacturing']?['editable'] == true ||
                m.state['cabinetAuthoring'] == true) &&
            {'undo', 'redo'}.contains(id))) {
      return false;
    }
    if (id == 'delete' && view.selectedDimensionId != null) {
      final d = (m.state['dimensions'] as List? ?? [])
          .cast<Json>()
          .where((d) => d['id'] == view.selectedDimensionId)
          .firstOrNull;
      return d != null &&
          m.visible(d['annotation'] as Json) &&
          !m.locked(d['annotation'] as Json);
    }
    if (id == 'paste' || id == 'pasteCurrentLayer') {
      return m.hasClipboard;
    }
    if ({'copy', 'cut'}.contains(id) && view.selectedDimensionId != null) {
      return true;
    }
    if ({'copy', 'cut', 'delete'}.contains(id)) return m.selection.isNotEmpty;
    if (id == 'undo') return m.state['canUndo'] == true;
    if (id == 'redo') return m.state['canRedo'] == true;
    return true;
  }

  Future<void> action(String id) async {
    if (manufacturing && id.startsWith('cam.')) {
      manufacturingActions[id.substring(4)]?.call();
      return;
    }
    if (manufacturing &&
        !{
          'open',
          'new',
          'save',
          'saveAs',
          'print',
          'undo',
          'redo',
          'all',
          'clear',
          'copy',
        }.contains(id)) {
      return;
    }
    try {
      await performAction(id);
    } finally {
      // Dialog/menu routes restore their button's focus by default. Return to
      // the drawing so the next shortcut works without another canvas click.
      if (mounted &&
          widget.active &&
          ModalRoute.of(context)?.isCurrent == true) {
        workspaceFocus.requestFocus();
      }
    }
  }

  Future<void> clipboardMenu(Offset position) async {
    final overlay = Overlay.of(context).context.findRenderObject() as RenderBox;
    final local = overlay.globalToLocal(position);
    final choice = await showMenu<String>(
      context: context,
      position: RelativeRect.fromRect(
        local & Size.zero,
        Offset.zero & overlay.size,
      ),
      items: [
        for (final entry in const {
          'copy': 'Copy',
          'cut': 'Cut',
          'paste': 'Paste',
          'pasteCurrentLayer': 'Paste to Current Layer',
          'copyItems': 'Copy dimensions / guides…',
        }.entries)
          PopupMenuItem(
            value: entry.key,
            enabled: editEnabled(entry.key),
            child: Text(entry.value),
          ),
      ],
    );
    if (!mounted) return;
    if (choice != null) await action(choice);
    workspaceFocus.requestFocus();
  }

  Future<void> performAction(String id) async {
    if (m.busy || !m.ready) return;
    if (pointEditor.active) {
      if (id == 'delete') {
        await pointEditor.edit('delete');
        return;
      }
      if (id == 'all') {
        pointEditor.selectAll();
        return;
      }
      if (id == 'cut') {
        pointEditor.hint =
            'Use Delete points to remove vertices, or Done to cut objects.';
        pointEditor.changed();
        return;
      }
      if (pointEditor.dragging) pointEditor.cancelDrag();
    }
    final selected = m.entities
        .where((e) => m.selection.contains(e['id']))
        .toList();
    switch (id) {
      case 'recovery':
        if (await m.run('recovery.list') && mounted) await offerRecovery();
      case 'open':
        if (widget.onDocument == null && !await confirmDiscard()) return;
        final path = await pick('open');
        if (path != null && widget.onDocument != null) {
          await widget.onDocument!('project.open', {'path': path});
          return;
        }
        if (path != null && await m.run('project.open', {'path': path})) {
          view.setTool('select');
          view.fit(
            bounds(m.state['bounds']) ?? const Rect.fromLTWH(0, 0, 96, 48),
          );
          if (mounted) await reviewOpeningRepair(context, m);
        }
      case 'new':
        if (widget.onDocument == null && !await confirmDiscard()) return;
        final value = await form('New drawing', {
          'Name': 'Untitled',
          'Units (in or mm)': 'in',
        });
        if (value != null) {
          if (!['in', 'mm'].contains(value['Units (in or mm)'])) return;
          if (widget.onDocument != null) {
            await widget.onDocument!('project.new', {
              'name': value['Name'],
              'units': value['Units (in or mm)'],
            });
            return;
          }
          await m.run('project.new', {
            'name': value['Name'],
            'units': value['Units (in or mm)'],
          });
          layer = '0';
          fitPlate();
        }
      case 'save':
        await save();
      case 'saveAs':
        await save(asNew: true);
      case 'export':
        final path = await pick('export');
        if (path != null) await m.run('project.export_dxf', {'path': path});
      case 'print':
        if (!(m.state['capabilities'] as List? ?? []).contains(
          'shop_drawing_pdf',
        )) {
          m.message =
              'Save your drawing and restart Flutter to load Shop Drawing PDF.';
          setState(() {});
          return;
        }
        if (m.readOnly) {
          m.message =
              'Machining jobs remain view-only in this evaluation build.';
          setState(() {});
          return;
        }
        await showDialog<void>(
          context: context,
          barrierDismissible: false,
          builder: (_) => ShopPdfDialog(model: m),
        );
      case 'openSvg':
        if (widget.onDocument == null || m.state['svgImport'] != true) return;
        final svgUnits = m.units;
        final newSvgPath = await pick('svg');
        if (!mounted || newSvgPath == null) return;
        await widget.onDocument!('svg.open', {
          'path': newSvgPath,
          'units': svgUnits,
        });
        break;
      case 'importSvg':
        if (m.state['svgImport'] != true || m.readOnly) return;
        final svgRevision = m.state['revision'];
        final svgPath = await pick('svg');
        if (!mounted || svgPath == null || m.state['revision'] != svgRevision) {
          return;
        }
        final addedSvg = await showDialog<bool>(
          context: context,
          barrierDismissible: false,
          builder: (_) => SvgImportDialog(model: m, path: svgPath),
        );
        if (addedSvg == true && mounted) {
          view.selectedDimensionId = null;
          view.fit(
            bounds(m.state['selectionBounds']) ??
                bounds(m.state['bounds']) ??
                const Rect.fromLTWH(0, 0, 96, 48),
          );
          setState(() {});
          await reviewOpeningRepair(
            context,
            m,
            entityIds: m.selection.toList(),
          );
        }
        break;
      case 'import':
        final path = await pick('open');
        if (path != null && await m.run('design.import', {'path': path})) {
          view.fit(
            bounds(m.state['bounds']) ?? const Rect.fromLTWH(0, 0, 96, 48),
          );
          if (mounted) {
            await reviewOpeningRepair(
              context,
              m,
              entityIds: m.selection.toList(),
            );
          }
        }
      case 'repairSuite':
        await showDialog<void>(
          context: context,
          barrierDismissible: false,
          builder: (_) => RepairSuiteDialog(model: m),
        );
      case 'checkContours':
        await reviewOpeningRepair(context, m, alwaysShow: true);
      case 'smallGeometry':
        if (m.state['smallGeometryReview'] != true) {
          m.message = 'Restart Flutter to load small-geometry review.';
          setState(() {});
          return;
        }
        await showDialog<void>(
          context: context,
          barrierDismissible: false,
          builder: (_) => SmallGeometryDialog(model: m),
        );
      case 'guides':
        if (m.state['guideAuthoring'] != true) {
          m.message = 'Restart Flutter to load construction guides.';
          setState(() {});
          return;
        }
        await showDialog<void>(
          context: context,
          barrierDismissible: false,
          builder: (_) => GuidesDialog(model: m, view: view),
        );
      case 'dimension':
      case 'dimensions':
        if (m.state['dimensionAuthoring'] != true) {
          m.message = 'Restart Flutter to load dimension authoring.';
          setState(() {});
          return;
        }
        if (id == 'dimensions') {
          await manageDimensions(context, m);
        } else {
          pointEditor.finish();
          view.setTool('dimension');
          view.hint =
              'Click two points for a dimension; nearby geometry features stay linked.';
          setState(() {});
        }
      case 'undo':
        view.setTool('select');
        await m.run('history.undo');
      case 'redo':
        view.setTool('select');
        await m.run('history.redo');
      case 'copyItems':
        final items = await chooseClipboardItems(
          context,
          m,
          dimensionId: view.selectedDimensionId,
        );
        if (items != null) {
          final cut = items.remove('cut') == true;
          await m.copyDrawing(cut: cut, params: items);
        }
      case 'copy':
        if (editEnabled(id)) {
          await m.copyDrawing(
            params: view.selectedDimensionId == null
                ? {}
                : {
                    'include_geometry': false,
                    'dimension_ids': [view.selectedDimensionId],
                  },
          );
        }
      case 'cut':
        if (editEnabled(id)) {
          await m.copyDrawing(
            cut: true,
            params: view.selectedDimensionId == null
                ? {}
                : {
                    'include_geometry': false,
                    'dimension_ids': [view.selectedDimensionId],
                  },
          );
        }
      case 'paste':
        if (editEnabled(id)) await m.pasteDrawing();
      case 'pasteCurrentLayer':
        if (editEnabled(id)) {
          await m.pasteDrawing({'target_layer': layer});
        }
      case 'delete':
        if (view.selectedDimensionId != null) {
          final d = (m.state['dimensions'] as List? ?? [])
              .cast<Json>()
              .where((d) => d['id'] == view.selectedDimensionId)
              .firstOrNull;
          if (d != null && await deleteDimension(context, m, d)) {
            view.selectedDimensionId = null;
            view.changed();
          }
        } else {
          await m.run('geometry.delete');
        }
      case 'all':
        await m.select(m.selectable.map((e) => e['id'] as String).toSet());
      case 'invert':
        await m.select(
          m.selectable
              .where((e) => !m.selection.contains(e['id']))
              .map((e) => e['id'] as String)
              .toSet(),
        );
      case 'clear':
        await m.select({});
        view.setTool('select');
      case 'select':
      case 'pan':
      case 'measure':
      case 'line':
      case 'rectangle':
      case 'circle':
      case 'ellipse':
      case 'slot':
      case 'polygon':
        view.setTool(id);
        setState(() {});
      case 'fit':
        final boxes = [
          bounds(m.state['bounds']),
          ...(m.state['dimensions'] as List? ?? []).map(
            (d) => bounds(d['bounds']),
          ),
        ].whereType<Rect>().toList();
        view.fit(
          boxes.isEmpty
              ? const Rect.fromLTWH(0, 0, 96, 48)
              : boxes.reduce((a, b) => a.expandToInclude(b)),
        );
      case 'plate':
        fitPlate();
      case 'move':
        await numeric(
          'Move / rotate selection',
          'geometry.transform',
          {'dx': '0', 'dy': '0', 'angle': '0'},
          note:
              'Distances in ${m.units}; rotation in degrees around the selection center.',
        );
      case 'scale':
        await numeric(
          'Scale selection',
          'design.scale',
          {'sx': '1', 'sy': '1'},
          note:
              'Factors about the selection center. Analytic circles require equal X/Y factors.',
        );
      case 'mirrorX':
        await m.run('geometry.mirror', {'axis': 'vertical'});
      case 'mirrorY':
        await m.run('geometry.mirror', {'axis': 'horizontal'});
      case 'duplicate':
        await numeric('Duplicate selection', 'design.duplicate', {
          'dx': '1',
          'dy': '1',
        });
      case 'resizeCircles':
        await numeric('Circle diameter', 'design.resize_circles', {
          'diameter': '0.25',
        });
      case 'circles':
        await numeric('Select circles by diameter', 'selection.circles', {
          'diameter': '0.25',
          'tolerance': '0.001',
        });
      case 'group':
      case 'ungroup':
      case 'compound':
      case 'uncompound':
      case 'merge':
      case 'explode':
        await m.run('design.$id');
      case 'union':
      case 'subtract_front':
      case 'subtract_back':
      case 'intersect':
      case 'exclude':
        final yes = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: Text(id.replaceAll('_', ' ')),
            content: const Text(
              'Replace the selected contours using the existing Python construction engine. For subtraction, front/back follows drawing order. This is one undoable action.',
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('Cancel'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(context, true),
                child: const Text('Apply'),
              ),
            ],
          ),
        );
        if (yes == true) await m.run('design.boolean', {'operation': id});
      case 'text':
      case 'editText':
        final source =
            id == 'editText' &&
                selected.length == 1 &&
                selected.first['kind'] == 'text'
            ? selected.first
            : null;
        if (id == 'editText' && source == null) return;
        final value = await form(source == null ? 'Add text' : 'Edit text', {
          'Text': source?['text'] ?? 'Text',
          'x': '${source?['insertion']?['x'] ?? 5}',
          'y': '${source?['insertion']?['y'] ?? 5}',
          'height': '${source?['height'] ?? 2}',
          'Font': source?['font_family'] ?? 'Arial',
        });
        if (value != null) {
          final x = double.tryParse(value['x']!),
              y = double.tryParse(value['y']!),
              h = double.tryParse(value['height']!);
          if (x != null &&
              y != null &&
              h != null &&
              x.isFinite &&
              y.isFinite &&
              h.isFinite) {
            await m.run(
              source == null ? 'geometry.add_text' : 'geometry.update_text',
              {
                if (source != null) 'entity_id': source['id'],
                'text': value['Text'],
                'x': x,
                'y': y,
                'height': h,
                'font_family': value['Font'],
                'layer': layer,
              },
            );
          }
        }
      case 'textCurves':
        await m.run('geometry.convert_text');
      case 'cleaveLine':
        if (m.readOnly || m.selection.isEmpty) return;
        view.setTool('cleaveLine');
        view.hint =
            'Pick two points across the selected closed shapes; Esc cancels.';
      case 'cleave':
        await showDialog<void>(
          context: context,
          barrierDismissible: false,
          builder: (_) => CleaveDialog(model: m),
        );
      case 'outline':
      case 'convertShape':
      case 'editableOutline':
      case 'joinRepair':
      case 'backtracks':
      case 'nearPoints':
        if (['joinRepair', 'backtracks', 'nearPoints'].contains(id) &&
            !(m.state['capabilities'] as List? ?? []).contains(
              'arrange_repair',
            )) {
          m.message =
              'Save your drawing and restart Flutter to load contour repair.';
          setState(() {});
          return;
        }
        if (!(m.state['capabilities'] as List? ?? []).contains('shape_tools')) {
          m.message =
              'Save your drawing and restart Flutter to load the new shape tools.';
          setState(() {});
          return;
        }
        await showDialog<void>(
          context: context,
          barrierDismissible: false,
          builder: (_) => ShapeToolsDialog(controller: m, tool: id),
        );
      case 'arrow':
      case 'double_arrow':
      case 'chevron':
      case 'star':
        await numeric(
          'Create ${id.replaceAll('_', ' ')}',
          'design.shape',
          {'x': '5', 'y': '5', 'width': '12', 'height': '8', 'sides': '5'},
          extra: {'kind': id, 'layer': layer},
        );
      case 'precise':
        final type = await showDialog<String>(
          context: context,
          builder: (context) => SimpleDialog(
            title: const Text('Draw by dimensions'),
            children: [
              for (final name in [
                'line',
                'rectangle',
                'circle',
                'ellipse',
                'slot',
                'polygon',
              ])
                SimpleDialogOption(
                  onPressed: () => Navigator.pop(context, name),
                  child: Text(name),
                ),
            ],
          ),
        );
        if (type == null) return;
        if (type == 'line') {
          await numeric(
            'Line · ${m.units}',
            'geometry.add_line',
            {'x1': '0', 'y1': '0', 'x2': '10', 'y2': '0'},
            extra: {'layer': layer},
          );
        } else if (type == 'circle') {
          await numeric(
            'Circle · ${m.units}',
            'geometry.add_circle',
            {'x': '10', 'y': '10', 'diameter': '5'},
            extra: {'layer': layer},
          );
        } else {
          await numeric(
            '$type · ${m.units}',
            type == 'rectangle' ? 'geometry.add_rectangle' : 'design.shape',
            {
              'x': '5',
              'y': '5',
              'width': '10',
              'height': '5',
              if (type == 'polygon') 'sides': '6',
            },
            extra: {'layer': layer, if (type != 'rectangle') 'kind': type},
          );
        }
      case 'points':
        if (m.readOnly) return;
        if (!(m.state['capabilities'] as List? ?? []).contains(
          'graphical_points',
        )) {
          m.message =
              'Save your drawing and restart Flutter to load graphical point editing.';
          setState(() {});
          return;
        }
        view.setTool('points');
        pointEditor.start();
        workspaceFocus.requestFocus();
        if (selected.length == 1) {
          await pointEditor.activate(selected.first['id'] as String);
        }
      case 'pushFit':
        if (!(m.state['capabilities'] as List? ?? []).contains(
          'push_through_fit',
        )) {
          m.message =
              'Save your drawing and restart Flutter to load Push-through fit.';
          setState(() {});
          return;
        }
        await showDialog<void>(
          context: context,
          barrierDismissible: false,
          builder: (_) => PushFitDialog(model: m),
        );
      case 'patchwork':
        if (!(m.state['capabilities'] as List? ?? []).contains('patchwork')) {
          m.message =
              'Save your drawing and restart Flutter to load Patchwork.';
          setState(() {});
          return;
        }
        await showDialog<void>(
          context: context,
          barrierDismissible: false,
          builder: (_) => PatchworkDialog(model: m),
        );
      case 'appearance':
        if (!(m.state['capabilities'] as List? ?? []).contains(
          'object_appearance',
        )) {
          m.message =
              'Save your drawing and restart Flutter to load Object Fill / Stroke.';
          setState(() {});
          return;
        }
        await showDialog<void>(
          context: context,
          builder: (_) => AppearanceDialog(model: m),
        );
      case 'pointCoordinates':
        if (selected.length != 1 || selected.first['kind'] != 'polyline') {
          m.message = 'Select one polyline to edit its points.';
          setState(() {});
          return;
        }
        final value = await form('Edit polyline points', {
          'Points (x,y per line)': (selected.first['points'] as List)
              .map((p) => '${p['x']},${p['y']}')
              .join('\n'),
        });
        if (value != null) {
          try {
            final points = value.values.first
                .split('\n')
                .where((s) => s.trim().isNotEmpty)
                .map((s) {
                  final xy = s.split(',');
                  return {'x': double.parse(xy[0]), 'y': double.parse(xy[1])};
                })
                .toList();
            await m.run('design.points', {'points': points});
          } catch (_) {
            m.message = 'Use one numeric x,y pair per line.';
            setState(() {});
          }
        }
      case 'cabinetAuthor':
        final cabinetPaths = await showDialog<List<String>>(
          context: context,
          builder: (_) => CabinetAuthoringDialog(model: m),
        );
        if (cabinetPaths != null) {
          for (final path in cabinetPaths) {
            if (!mounted) break;
            await widget.onDocument?.call('project.open', {'path': path});
          }
        }
        break;
      case 'mailerAuthor':
        if (m.state['mailerAuthoring'] != true || m.readOnly) return;
        final createdMailer = await showDialog<bool>(
          context: context,
          barrierDismissible: false,
          builder: (_) => MailerDialog(model: m),
        );
        if (createdMailer == true && mounted) {
          view.selectedDimensionId = null;
          view.fit(
            bounds(m.state['selectionBounds']) ??
                bounds(m.state['bounds']) ??
                const Rect.fromLTWH(0, 0, 96, 48),
          );
          setState(() {});
        }
        break;
      case 'panAuthor':
        await showDialog<void>(
          context: context,
          builder: (_) => PanAuthoringDialog(model: m),
        );
        break;
      case 'panReview':
      case 'cabinetReview':
        final handoff = await showDialog<bool>(
          context: context,
          builder: (_) =>
              AssemblyReviewDialog(model: m, cabinet: id == 'cabinetReview'),
        );
        if (mounted && handoff == true) switchWorkspace(true);
        break;
      case 'repeat':
        await showDialog<void>(
          context: context,
          builder: (_) => RepeatDialog(model: m),
        );
        break;
      case 'cutoff':
        await showDialog<void>(
          context: context,
          builder: (_) => SheetCutoffDialog(model: m),
        );
      case 'nest':
        await numeric(
          'Auto nest',
          'geometry.auto_nest',
          {'spacing': '0.25'},
          note:
              'Uses the existing Python bounding-box nesting engine. Applies to selection, or all editable geometry when empty. Plates with Patchwork are restricted to their physical pieces; no overflow plates are added.',
        );
      case 'properties':
        setState(() => inspector = !inspector);
        saveLayout();
      default:
        if (id.startsWith('align_') || id.startsWith('space_')) {
          if (!(m.state['capabilities'] as List? ?? []).contains(
            'arrange_repair',
          )) {
            m.message =
                'Save your drawing and restart Flutter to load arrangement previews.';
            setState(() {});
            return;
          }
          await showDialog<void>(
            context: context,
            barrierDismissible: false,
            builder: (_) => ShapeToolsDialog(controller: m, tool: id),
          );
        }
    }
  }

  @override
  Widget build(BuildContext context) {
    final shortcuts = <ShortcutActivator, VoidCallback>{
      const SingleActivator(LogicalKeyboardKey.keyO, control: true): () =>
          action('open'),
      const SingleActivator(LogicalKeyboardKey.keyN, control: true): () =>
          action('new'),
      const SingleActivator(LogicalKeyboardKey.keyS, control: true): () =>
          action('save'),
      const SingleActivator(LogicalKeyboardKey.keyP, control: true): () =>
          action('print'),
      const SingleActivator(
        LogicalKeyboardKey.keyS,
        control: true,
        shift: true,
      ): () =>
          action('saveAs'),
      const SingleActivator(LogicalKeyboardKey.keyZ, control: true): () =>
          action('undo'),
      const SingleActivator(LogicalKeyboardKey.keyY, control: true): () =>
          action('redo'),
      const SingleActivator(
        LogicalKeyboardKey.keyZ,
        control: true,
        shift: true,
      ): () =>
          action('redo'),
      const SingleActivator(LogicalKeyboardKey.keyA, control: true): () =>
          action('all'),
      const SingleActivator(LogicalKeyboardKey.keyC, control: true): () =>
          action('copy'),
      const SingleActivator(LogicalKeyboardKey.keyV, control: true): () =>
          action('paste'),
      const SingleActivator(LogicalKeyboardKey.keyX, control: true): () =>
          action('cut'),
      const SingleActivator(LogicalKeyboardKey.delete): () => action('delete'),
      const SingleActivator(LogicalKeyboardKey.escape): escape,
      const SingleActivator(LogicalKeyboardKey.arrowLeft): () => nudge(-1, 0),
      const SingleActivator(LogicalKeyboardKey.arrowRight): () => nudge(1, 0),
      const SingleActivator(LogicalKeyboardKey.arrowUp): () => nudge(0, 1),
      const SingleActivator(LogicalKeyboardKey.arrowDown): () => nudge(0, -1),
    };
    return CallbackShortcuts(
      bindings: shortcuts,
      child: Focus(
        focusNode: workspaceFocus,
        autofocus: true,
        child: Scaffold(
          body: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              header(),
              if (manufacturing)
                Expanded(
                  child: ManufacturingWorkspace(
                    model: m,
                    onSave: () => save(asNew: true),
                    view: view,
                    panelColor: panel,
                    inspectorOpen: inspector,
                    onInspectorChanged: (value) {
                      setState(() => inspector = value);
                      saveLayout();
                    },
                    shellBuilder: manufacturingShell,
                    documentBarBuilder: (toggle) =>
                        documentStrip(viewSwitch: toggle),
                  ),
                )
              else ...[
                menus(),
                ribbon(),
                stockStrip(),
                ...documentNotices(),
                Expanded(
                  child: LayoutBuilder(
                    builder: (context, c) => Stack(
                      key: stackKey,
                      children: [
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            sideTools(),
                            Expanded(
                              child: Column(
                                children: [
                                  documentStrip(),
                                  if (pointEditor.active) pointControls(),
                                  Expanded(
                                    child: DesignCanvas(
                                      model: m,
                                      view: view,
                                      layer: layer,
                                      dark: widget.dark,
                                      onFocus: workspaceFocus.requestFocus,
                                      onContextMenu: clipboardMenu,
                                      pointEditor: pointEditor,
                                      onCleave: (line) => showDialog<void>(
                                        context: context,
                                        barrierDismissible: false,
                                        builder: (_) =>
                                            CleaveDialog(model: m, line: line),
                                      ),
                                      onDimension: (references) =>
                                          showDialog<void>(
                                            context: context,
                                            barrierDismissible: false,
                                            builder: (_) => DimensionDialog(
                                              model: m,
                                              references: references,
                                            ),
                                          ),
                                    ),
                                  ),
                                ],
                              ),
                            ),
                            if (inspector && c.maxWidth >= 850)
                              SizedBox(width: 246, child: inspectorPanel()),
                          ],
                        ),
                        for (final entry in floating.entries)
                          Positioned(
                            left: entry.value.dx.clamp(
                              0,
                              math.max(0, c.maxWidth - 300),
                            ),
                            top: entry.value.dy.clamp(
                              0,
                              math.max(0, c.maxHeight - 180),
                            ),
                            child: SizedBox(
                              width: 300,
                              child: floatingPanel(entry.key),
                            ),
                          ),
                        if (!m.ready)
                          Positioned.fill(
                            child: Container(
                              color: panel.withValues(alpha: .95),
                              child: Center(
                                child: Padding(
                                  padding: const EdgeInsets.all(40),
                                  child: Column(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      if (m.busy)
                                        const CircularProgressIndicator(),
                                      const SizedBox(height: 20),
                                      SelectableText(m.message),
                                      if (!m.busy)
                                        TextButton(
                                          onPressed: () => m.start(),
                                          child: const Text(
                                            'Retry engine startup',
                                          ),
                                        ),
                                    ],
                                  ),
                                ),
                              ),
                            ),
                          ),
                      ],
                    ),
                  ),
                ),
              ],
              canvasStatus(),
              statusBar(),
            ],
          ),
        ),
      ),
    );
  }

  Widget header() => Container(
    height: 36,
    color: panel,
    padding: const EdgeInsets.symmetric(horizontal: 16),
    child: Row(
      children: [
        const Icon(Icons.architecture, color: designTeal, size: 27),
        const SizedBox(width: 9),
        const Text(
          'workbench',
          style: TextStyle(
            fontSize: 18,
            fontWeight: FontWeight.w700,
            letterSpacing: -.7,
          ),
        ),
        const SizedBox(width: 18),
        for (final entry in [(false, 'Design'), (true, 'Manufacturing')])
          TextButton(
            key: ValueKey('workspace-${entry.$2.toLowerCase()}'),
            onPressed: m.ready && !m.busy && !pointEditor.active
                ? () => switchWorkspace(entry.$1)
                : null,
            style: TextButton.styleFrom(
              backgroundColor: manufacturing == entry.$1
                  ? designTeal.withValues(alpha: .1)
                  : null,
              textStyle: Theme.of(context).textTheme.labelLarge!.copyWith(
                fontWeight: manufacturing == entry.$1
                    ? FontWeight.bold
                    : FontWeight.normal,
              ),
            ),
            child: Text(entry.$2),
          ),
        const Spacer(),
        if (developmentReloadEnabled)
          ReloadUiButton(enabled: m.ready && !m.busy),
        IconButton(
          tooltip: 'Reset workspace layout',
          onPressed: () {
            setState(() {
              order = groups.keys.toList();
              floating.clear();
              inspector = true;
              showLayers = true;
            });
            saveLayout();
          },
          icon: const Icon(Icons.view_quilt_outlined, size: 18),
        ),
        IconButton(
          tooltip: 'Toggle light / dark',
          onPressed: () {
            widget.onTheme(!widget.dark);
            Future<void>.delayed(Duration.zero, saveLayout);
          },
          icon: Icon(
            widget.dark ? Icons.light_mode_outlined : Icons.dark_mode_outlined,
            size: 18,
          ),
        ),
        if (MediaQuery.sizeOf(context).width >= 1000) ...[
          const SizedBox(width: 8),
          Text(
            developmentReloadEnabled ? 'Development' : 'Evaluation build',
            style: TextStyle(fontSize: 11, color: Theme.of(context).hintColor),
          ),
        ],
      ],
    ),
  );
  Widget menus() => Material(
    key: const ValueKey('menu-bar'),
    color: panel,
    child: SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: [
          for (final group in groups.keys)
            PopupMenuButton<String>(
              tooltip: '$group menu',
              constraints: const BoxConstraints(minWidth: 280, maxWidth: 420),
              onSelected: action,
              itemBuilder: (context) => [
                for (final c in groups[group]!)
                  PopupMenuItem(
                    value: c.id,
                    enabled: enabled(c),
                    child: Row(
                      children: [
                        Icon(c.icon, size: 17),
                        const SizedBox(width: 10),
                        Text(c.label),
                        if (shortcutLabels.containsKey(c.id)) ...[
                          const SizedBox(width: 16),
                          Text(
                            shortcutLabels[c.id]!,
                            style: const TextStyle(
                              fontSize: 11,
                              color: Colors.grey,
                            ),
                          ),
                        ],
                        if (c.pending)
                          const Text(
                            '  · not ported',
                            style: TextStyle(fontSize: 10),
                          ),
                      ],
                    ),
                  ),
              ],
              child: Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 5,
                ),
                child: Text(group, style: const TextStyle(fontSize: 12)),
              ),
            ),
          PopupMenuButton<String>(
            tooltip: 'Edit menu',
            constraints: const BoxConstraints(minWidth: 320, maxWidth: 420),
            onSelected: action,
            itemBuilder: (context) => [
              for (final id in [
                'undo',
                'redo',
                'copy',
                'cut',
                'paste',
                'pasteCurrentLayer',
                'copyItems',
                'delete',
                'all',
                'clear',
              ])
                PopupMenuItem(
                  value: id,
                  enabled: editEnabled(id),
                  child: Row(
                    children: [
                      Expanded(
                        child: Text(
                          id == 'copyItems'
                              ? 'Copy dimensions / guides...'
                              : id == 'pasteCurrentLayer'
                              ? 'Paste to Current Layer'
                              : '${id[0].toUpperCase()}${id.substring(1)}',
                        ),
                      ),
                      const SizedBox(width: 16),
                      Text(
                        shortcutLabels[id] ?? '',
                        style: const TextStyle(
                          fontSize: 11,
                          color: Colors.grey,
                        ),
                      ),
                    ],
                  ),
                ),
            ],
            child: const Padding(
              padding: EdgeInsets.symmetric(horizontal: 16, vertical: 5),
              child: Text('Edit', style: TextStyle(fontSize: 12)),
            ),
          ),
        ],
      ),
    ),
  );
  Widget commandButton(DesignCommand c, {bool labelled = false}) => Tooltip(
    message: c.pending ? '${c.label} · not ported yet' : c.label,
    child: labelled
        ? SizedBox(
            width: 132,
            child: TextButton.icon(
              onPressed: enabled(c) ? () => action(c.id) : null,
              icon: Icon(c.icon, size: 18),
              label: Text(c.label, style: const TextStyle(fontSize: 11)),
              style: TextButton.styleFrom(
                alignment: Alignment.centerLeft,
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 8),
              ),
            ),
          )
        : IconButton(
            key: ValueKey('command-${c.id}'),
            onPressed: enabled(c) ? () => action(c.id) : null,
            icon: Icon(c.icon, size: 20),
            style: IconButton.styleFrom(
              minimumSize: const Size(33, 32),
              padding: const EdgeInsets.all(6),
            ),
          ),
  );
  Widget ribbon() => DragTarget<String>(
    onWillAcceptWithDetails: (d) => groups.containsKey(d.data),
    onAcceptWithDetails: (d) {
      setState(() => floating.remove(d.data));
      saveLayout();
    },
    builder: (context, accepted, rejected) => Container(
      key: const ValueKey('toolbar-dock'),
      height: floating.length == order.length ? 24 : 58,
      color: accepted.isNotEmpty ? designTeal.withValues(alpha: .2) : panel,
      child: floating.length == order.length
          ? const Align(
              alignment: Alignment.centerLeft,
              child: Padding(
                padding: EdgeInsets.symmetric(horizontal: 16),
                child: Text(
                  'Drop a palette here to dock',
                  style: TextStyle(fontSize: 11),
                ),
              ),
            )
          : SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Row(
                children: [
                  for (final group in order.where(
                    (g) => !floating.containsKey(g),
                  ))
                    DragTarget<String>(
                      onWillAcceptWithDetails: (d) => d.data != group,
                      onAcceptWithDetails: (d) {
                        setState(() {
                          order.remove(d.data);
                          order.insert(order.indexOf(group), d.data);
                          floating.remove(d.data);
                        });
                        saveLayout();
                      },
                      builder: (context, accepted, rejected) => Container(
                        key: ValueKey('group-$group'),
                        margin: const EdgeInsets.only(right: 6),
                        padding: const EdgeInsets.symmetric(horizontal: 4),
                        decoration: BoxDecoration(
                          color: accepted.isNotEmpty
                              ? designTeal.withValues(alpha: .15)
                              : widget.dark
                              ? const Color(0xff2b3e42)
                              : const Color(0xfff2f5f3),
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Draggable<String>(
                              data: group,
                              feedback: paletteDragPreview(group),
                              onDragEnd: (d) => finishPaletteDrag(group, d),
                              child: MouseRegion(
                                cursor: SystemMouseCursors.grab,
                                child: Container(
                                  key: ValueKey('grip-$group'),
                                  width: 160,
                                  height: 22,
                                  color: Colors.transparent,
                                  child: Row(
                                    children: [
                                      const Icon(
                                        Icons.drag_indicator,
                                        size: 14,
                                        color: Colors.grey,
                                      ),
                                      const SizedBox(width: 4),
                                      Text(
                                        key: ValueKey('group-label-$group'),
                                        group.toUpperCase(),
                                        style: const TextStyle(
                                          fontSize: 9,
                                          letterSpacing: 1,
                                        ),
                                      ),
                                      const Spacer(),
                                      IconButton(
                                        tooltip: 'Float $group palette',
                                        onPressed: () {
                                          setState(
                                            () => floating[group] =
                                                const Offset(90, 70),
                                          );
                                          saveLayout();
                                        },
                                        icon: const Icon(
                                          Icons.open_in_new,
                                          size: 12,
                                        ),
                                        style: IconButton.styleFrom(
                                          minimumSize: const Size(24, 20),
                                          padding: EdgeInsets.zero,
                                          tapTargetSize:
                                              MaterialTapTargetSize.shrinkWrap,
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                              ),
                            ),
                            Row(
                              children: [
                                for (final c in groups[group]!.take(4))
                                  commandButton(c),
                                PopupMenuButton<String>(
                                  tooltip: 'More $group',
                                  onSelected: action,
                                  itemBuilder: (context) => [
                                    for (final c in groups[group]!.skip(4))
                                      PopupMenuItem(
                                        value: c.id,
                                        enabled: enabled(c),
                                        child: Text(
                                          c.pending
                                              ? '${c.label} · not ported'
                                              : c.label,
                                        ),
                                      ),
                                  ],
                                  padding: EdgeInsets.zero,
                                  constraints: const BoxConstraints(
                                    minWidth: 24,
                                    minHeight: 28,
                                  ),
                                  child: const SizedBox(
                                    width: 28,
                                    height: 32,
                                    child: Icon(Icons.expand_more, size: 16),
                                  ),
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
  );
  Widget floatingPanel(String group) => Material(
    elevation: 9,
    color: panel,
    borderRadius: BorderRadius.circular(9),
    clipBehavior: Clip.antiAlias,
    child: Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Draggable<String>(
          data: group,
          feedback: paletteDragPreview(group),
          onDragEnd: (d) => finishPaletteDrag(group, d),
          childWhenDragging: const SizedBox(height: 32),
          child: MouseRegion(
            key: ValueKey('palette-grip-$group'),
            cursor: SystemMouseCursors.move,
            child: Container(
              color: designTeal.withValues(alpha: .12),
              padding: const EdgeInsets.only(left: 10),
              child: Row(
                children: [
                  const Icon(Icons.drag_indicator, size: 16),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      group,
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                  ),
                  IconButton(
                    key: ValueKey('dock-$group'),
                    tooltip: 'Dock $group',
                    onPressed: () {
                      setState(() => floating.remove(group));
                      saveLayout();
                    },
                    icon: const Icon(Icons.vertical_align_top, size: 18),
                  ),
                ],
              ),
            ),
          ),
        ),
        ConstrainedBox(
          constraints: const BoxConstraints(maxHeight: 320),
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(8),
            child: Wrap(
              children: [
                for (final c in groups[group]!)
                  commandButton(c, labelled: true),
              ],
            ),
          ),
        ),
      ],
    ),
  );
  Future<void> manageLayers() => showDialog<void>(
    context: context,
    builder: (context) => LayersDialog(
      model: m,
      current: layer,
      onCurrent: (name) => setState(() => layer = name),
    ),
  );

  List<Widget> documentNotices() => [
    if (m.state['recovery']?['warning'] case final String warning)
      Container(
        color: Colors.amber.withValues(alpha: .15),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
        child: Row(
          children: [
            Expanded(
              child: Text(
                'Recovery copy is not current. Save As to protect your work. $warning',
                style: const TextStyle(fontSize: 11),
              ),
            ),
            TextButton(
              onPressed: m.ready && !m.busy
                  ? () => m.run('recovery.checkpoint')
                  : null,
              child: const Text('Retry recovery'),
            ),
          ],
        ),
      ),
    if (m.readOnly)
      Container(
        width: double.infinity,
        color: Colors.amber.withValues(alpha: .15),
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 6),
        child: Text(
          m.state['readOnlyReason'] ??
              'This job contains ${m.state['operationCount']} machining operations. Associated geometry is protected; use Manufacturing to review and edit operations.',
          style: const TextStyle(fontSize: 11),
        ),
      ),
  ];

  Widget stockStrip() => Container(
    key: const ValueKey('stock-bar'),
    height: 34,
    color: panel,
    padding: const EdgeInsets.symmetric(horizontal: 16),
    child: SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: [
          const Text(
            'STOCK',
            style: TextStyle(fontSize: 10, fontWeight: FontWeight.w700),
          ),
          const SizedBox(width: 9),
          TextButton(
            onPressed: manufacturing
                ? manufacturingActions['stock']
                : m.readOnly
                ? null
                : () => numeric(
                    'Stock / plate size',
                    'plate.update',
                    {
                      'width': '${activePlate?['width'] ?? 96}',
                      'height': '${activePlate?['height'] ?? 48}',
                    },
                    extra: {'number': m.state['activePlate'] ?? 1},
                  ),
            child: Text(
              m.plates.isEmpty
                  ? '96 × 48'
                  : '${activePlate?['width']} × ${activePlate?['height']} ${m.units}',
            ),
          ),
          const SizedBox(width: 16),
          StockMaterialControls(model: m),
          const SizedBox(width: 16),
          const Text(
            'PLATE',
            style: TextStyle(fontSize: 10, fontWeight: FontWeight.w700),
          ),
          const SizedBox(width: 8),
          DropdownButton<int>(
            isDense: true,
            value: m.plates.any((p) => p['number'] == m.state['activePlate'])
                ? m.state['activePlate'] as int
                : null,
            items: [
              for (final p in m.plates)
                DropdownMenuItem(
                  value: p['number'] as int,
                  child: Text(
                    '${p['number']} · ${p['name'] ?? 'Plate'}',
                    style: const TextStyle(fontSize: 11),
                  ),
                ),
            ],
            onChanged: (n) async {
              await m.run('plate.set_active', {'number': n});
              fitPlate();
            },
            underline: const SizedBox(),
          ),
          IconButton(
            tooltip: 'Add plate',
            onPressed: m.readOnly
                ? null
                : () => numeric('Add plate', 'plate.add', {
                    'width': '96',
                    'height': '48',
                  }),
            icon: const Icon(Icons.add, size: 16),
          ),
          const SizedBox(width: 16),
          const Text(
            'DRAWING LAYER',
            style: TextStyle(fontSize: 10, fontWeight: FontWeight.w700),
          ),
          const SizedBox(width: 8),
          PopupMenuButton<String>(
            key: const ValueKey('drawing-layer-menu'),
            tooltip: 'Choose the layer for new objects',
            position: PopupMenuPosition.under,
            onSelected: (name) => setState(() => layer = name),
            itemBuilder: (context) => [
              const PopupMenuItem<String>(
                enabled: false,
                child: Text('Draw new objects on:'),
              ),
              for (final name in {
                '0',
                ...m.layers.map((l) => l['name'] as String),
              })
                PopupMenuItem(
                  value: name,
                  enabled:
                      !m.busy &&
                      !m.layers.any(
                        (l) =>
                            l['name'] == name &&
                            (l['locked'] == true || l['visible'] == false),
                      ),
                  child: Row(
                    children: [
                      Icon(
                        name == layer ? Icons.check : Icons.layers_outlined,
                        size: 16,
                      ),
                      const SizedBox(width: 8),
                      Text(layerLabel(name)),
                    ],
                  ),
                ),
            ],
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8),
              child: Row(
                children: [
                  ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 130),
                    child: Text(
                      layerLabel(layer),
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 11),
                    ),
                  ),
                  const Icon(Icons.arrow_drop_down, size: 20),
                ],
              ),
            ),
          ),
          TextButton.icon(
            onPressed: m.ready ? manageLayers : null,
            icon: const Icon(Icons.layers_outlined, size: 16),
            label: const Text('Layers…'),
          ),
          const SizedBox(width: 20),
          TextButton.icon(
            onPressed: () {
              view.grid = !view.grid;
              view.changed();
              setState(() {});
              saveLayout();
            },
            icon: Icon(
              Icons.grid_4x4,
              size: 16,
              color: view.grid ? designTeal : Colors.grey,
            ),
            label: const Text('Grid'),
          ),
          TextButton.icon(
            onPressed: () {
              view.snap = !view.snap;
              view.snapTarget = null;
              view.changed();
              setState(() {});
              saveLayout();
            },
            icon: Icon(
              Icons.grid_on,
              size: 16,
              color: view.snap ? designTeal : Colors.grey,
            ),
            label: Text(
              'Snap ${m.units == 'mm' ? '3.175 mm' : '⅛ in'} / objects',
            ),
          ),
        ],
      ),
    ),
  );
  Widget sideTools() => Container(
    width: 45,
    color: panel,
    child: SingleChildScrollView(
      child: Column(
        children: [
          for (final (id, icon, label) in [
            ('select', Icons.near_me_outlined, 'Select'),
            ('pan', Icons.pan_tool_outlined, 'Pan'),
            ('plate', Icons.crop_landscape, 'Fit plate'),
            ('fit', Icons.fit_screen, 'Fit drawing'),
            ('line', Icons.show_chart, 'Line'),
            ('rectangle', Icons.crop_square, 'Rectangle'),
            ('circle', Icons.circle_outlined, 'Circle'),
            ('ellipse', Icons.panorama_wide_angle_outlined, 'Ellipse'),
            ('measure', Icons.straighten, 'Measure'),
          ])
            Tooltip(
              message: label,
              child: IconButton(
                onPressed: () => action(id),
                icon: Icon(icon, size: 20),
                style: IconButton.styleFrom(
                  backgroundColor: view.tool == id
                      ? designTeal.withValues(alpha: .13)
                      : Colors.transparent,
                ),
              ),
            ),
        ],
      ),
    ),
  );
  Widget pointControls() => Container(
    width: double.infinity,
    color: panel,
    padding: const EdgeInsets.symmetric(horizontal: 8),
    child: SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: [
          Text(
            'POINTS · ${pointEditor.selected.length} selected',
            style: const TextStyle(fontSize: 11),
          ),
          const SizedBox(width: 8),
          TextButton(
            onPressed: pointEditor.canEdit ? pointEditor.selectAll : null,
            child: const Text('Select all points'),
          ),
          TextButton(
            onPressed: pointEditor.canEdit ? pointEditor.toggleInsert : null,
            child: Text(pointEditor.inserting ? 'Cancel add' : 'Add point'),
          ),
          TextButton(
            onPressed: pointEditor.canEdit && pointEditor.selected.isNotEmpty
                ? () => pointEditor.edit('delete')
                : null,
            child: const Text('Delete points'),
          ),
          if (pointEditor.dragging)
            TextButton(
              onPressed: pointEditor.cancelDrag,
              child: const Text('Cancel drag'),
            ),
          TextButton(
            onPressed: () => view.setTool('select'),
            child: const Text('Done'),
          ),
        ],
      ),
    ),
  );

  Widget documentStrip({Widget? viewSwitch}) => Container(
    height: 35,
    color: panel,
    padding: const EdgeInsets.symmetric(horizontal: 12),
    child: Row(
      children: [
        const Icon(Icons.description_outlined, size: 15, color: designTeal),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            '${m.state['name'] ?? 'Untitled'}${m.dirty ? ' •' : ''}',
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
          ),
        ),
        ?viewSwitch,
        if (viewSwitch == null)
          Text(
            m.readOnly ? 'VIEW ONLY' : 'DESIGN',
            style: const TextStyle(fontSize: 9, color: Colors.grey),
          ),
        IconButton(
          tooltip: 'Inspector',
          onPressed: () {
            setState(() => inspector = !inspector);
            saveLayout();
          },
          icon: const Icon(Icons.tune, size: 16),
        ),
      ],
    ),
  );
  Widget canvasStatus() => DesignHistoryBar(
    model: m,
    view: view,
    color: panel,
    undo: () => action('undo'),
    redo: () => action('redo'),
  );
  Widget inspectorPanel() => Material(
    color: panel,
    child: ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const Text(
          'Inspector',
          style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 5),
        Text(
          '${m.selection.length} selected · ${m.entities.length} objects',
          style: const TextStyle(fontSize: 11, color: Colors.grey),
        ),
        const Divider(height: 28),
        const Text(
          'SELECTION',
          style: TextStyle(fontSize: 10, letterSpacing: 1, color: Colors.grey),
        ),
        const SizedBox(height: 10),
        GeometryInspector(
          key: ValueKey((m.selection.toList()..sort()).join('|')),
          model: m,
          onAction: action,
        ),
        const Divider(height: 28),
        Row(
          children: [
            const Text(
              'LAYERS',
              style: TextStyle(
                fontSize: 10,
                letterSpacing: 1,
                color: Colors.grey,
              ),
            ),
            const Spacer(),
            IconButton(
              tooltip: 'Manage layers',
              onPressed: m.ready ? manageLayers : null,
              icon: const Icon(Icons.settings_outlined, size: 16),
            ),
          ],
        ),
        for (final l in m.layers)
          Row(
            children: [
              Expanded(
                child: Text(
                  l['name'],
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 11),
                ),
              ),
              Text(
                '${l['objects'] ?? 0}',
                style: const TextStyle(fontSize: 10, color: Colors.grey),
              ),
              IconButton(
                tooltip: 'Visibility: ${l['name']}',
                onPressed: m.readOnly
                    ? null
                    : () => m.run('layer.set', {
                        'name': l['name'],
                        'visible': l['visible'] == false,
                      }),
                icon: Icon(
                  l['visible'] == false
                      ? Icons.visibility_off_outlined
                      : Icons.visibility_outlined,
                  size: 15,
                ),
              ),
              IconButton(
                tooltip: 'Lock: ${l['name']}',
                onPressed: m.readOnly
                    ? null
                    : () => m.run('layer.set', {
                        'name': l['name'],
                        'locked': l['locked'] != true,
                      }),
                icon: Icon(
                  l['locked'] == true ? Icons.lock_outline : Icons.lock_open,
                  size: 15,
                ),
              ),
            ],
          ),
        if (m.selection.isNotEmpty)
          TextButton(
            onPressed: m.readOnly
                ? null
                : () => m.run('layer.move_selection', {'target': layer}),
            child: Text(
              'Move selection to $layer',
              style: const TextStyle(fontSize: 11),
            ),
          ),
        const Divider(height: 28),
        const Text(
          'WORKSPACE',
          style: TextStyle(fontSize: 10, letterSpacing: 1, color: Colors.grey),
        ),
        const SizedBox(height: 9),
        const Text(
          'Drag a group heading to reorder it or float it over the drawing. Drag a floating heading back to the toolbar to dock it. The arrow buttons also float and dock groups.',
          style: TextStyle(fontSize: 11, height: 1.6),
        ),
        const SizedBox(height: 18),
        Text(
          'Python core ${m.state['backend'] ?? '…'}',
          style: const TextStyle(fontSize: 9, color: Colors.grey),
        ),
      ],
    ),
  );
  Widget property(String label, String value) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 5),
    child: Row(
      children: [
        Text(label, style: const TextStyle(fontSize: 11, color: Colors.grey)),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            value,
            textAlign: TextAlign.right,
            style: const TextStyle(fontSize: 11),
          ),
        ),
      ],
    ),
  );
  Widget statusBar() => DesignStatusBar(model: m, view: view, color: panel);
}
