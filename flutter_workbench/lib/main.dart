import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';
import 'design/workspace.dart';
import 'drawing.dart';
import 'model.dart';

void main() => runApp(kIsWeb ? const WorkbenchApp() : const DesignApp());

class WorkbenchApp extends StatefulWidget {
  const WorkbenchApp({super.key});
  @override
  State<WorkbenchApp> createState() => _WorkbenchAppState();
}

class _WorkbenchAppState extends State<WorkbenchApp> {
  final model = WorkbenchModel();
  @override
  void dispose() {
    model.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: model,
    builder: (context, _) => MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'CAM Workbench · Flutter prototype',
      theme: ThemeData(
        useMaterial3: true,
        brightness: model.dark ? Brightness.dark : Brightness.light,
        colorScheme: ColorScheme.fromSeed(
          seedColor: teal,
          brightness: model.dark ? Brightness.dark : Brightness.light,
        ),
        scaffoldBackgroundColor: model.dark
            ? const Color(0xff17272b)
            : const Color(0xfff5f7f6),
        fontFamily: 'Segoe UI',
        textTheme: const TextTheme(
          bodyMedium: TextStyle(fontSize: 13),
          bodySmall: TextStyle(fontSize: 11),
        ),
        dividerColor: model.dark ? const Color(0xff34464a) : line,
        inputDecorationTheme: InputDecorationTheme(
          isDense: true,
          filled: true,
          fillColor: model.dark
              ? const Color(0xff26383c)
              : const Color(0xfff4f6f5),
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 12,
            vertical: 13,
          ),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(7),
            borderSide: BorderSide.none,
          ),
        ),
        iconButtonTheme: IconButtonThemeData(
          style: IconButton.styleFrom(
            minimumSize: const Size(34, 34),
            iconSize: 18,
          ),
        ),
      ),
      home: WorkbenchHome(model: model),
    ),
  );
}

class WorkbenchHome extends StatefulWidget {
  const WorkbenchHome({super.key, required this.model});
  final WorkbenchModel model;
  @override
  State<WorkbenchHome> createState() => _WorkbenchHomeState();
}

class _WorkbenchHomeState extends State<WorkbenchHome> {
  final transform = TransformationController();
  WorkbenchModel get m => widget.model;
  Color get panel => m.dark ? const Color(0xff1e3034) : Colors.white;
  @override
  void dispose() {
    transform.dispose();
    super.dispose();
  }

  void zoom(double factor) {
    final scale = transform.value.getMaxScaleOnAxis();
    final next = (scale * factor).clamp(.65, 5.0);
    transform.value = Matrix4.identity()..scaleByDouble(next, next, 1, 1);
  }

  Widget badge(String text, {Color color = teal}) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
    decoration: BoxDecoration(
      color: color.withValues(alpha: .1),
      borderRadius: BorderRadius.circular(5),
    ),
    child: Text(
      text,
      style: TextStyle(
        fontSize: 10,
        fontWeight: FontWeight.w700,
        color: color,
        letterSpacing: .5,
      ),
    ),
  );
  Widget section(String text) => Padding(
    padding: const EdgeInsets.only(top: 22, bottom: 12),
    child: Text(
      text,
      style: const TextStyle(
        fontSize: 10,
        fontWeight: FontWeight.w700,
        letterSpacing: 1.4,
        color: muted,
      ),
    ),
  );

  @override
  Widget build(BuildContext context) => Scaffold(
    body: LayoutBuilder(
      builder: (context, box) {
        final narrow = box.maxWidth < 1100;
        final tiny = box.maxWidth < 760;
        return Column(
          children: [
            _header(narrow),
            _workspaceBar(narrow),
            Expanded(
              child: Row(
                children: [
                  if (!tiny && !narrow && m.layersOpen)
                    SizedBox(width: narrow ? 208 : 234, child: _sidebar()),
                  Expanded(
                    child: Column(
                      children: [
                        _documentBar(tiny),
                        Expanded(
                          child: m.workspace == 2 ? _review() : _canvas(),
                        ),
                        if (!tiny) _canvasFooter(),
                      ],
                    ),
                  ),
                  if (!tiny)
                    SizedBox(width: narrow ? 248 : 282, child: _inspector()),
                ],
              ),
            ),
            _status(tiny),
          ],
        );
      },
    ),
  );

  Widget _header(bool tiny) => Container(
    height: 66,
    color: panel,
    padding: const EdgeInsets.symmetric(horizontal: 20),
    child: Row(
      children: [
        Container(
          width: 32,
          height: 32,
          decoration: BoxDecoration(
            color: teal,
            borderRadius: BorderRadius.circular(8),
          ),
          child: const Icon(Icons.architecture, color: Colors.white, size: 23),
        ),
        const SizedBox(width: 10),
        const Text(
          'workbench',
          style: TextStyle(
            fontSize: 21,
            fontWeight: FontWeight.w700,
            letterSpacing: -.7,
          ),
        ),
        if (!tiny) ...[
          const SizedBox(width: 14),
          badge('FLUTTER / PROTOTYPE'),
          const SizedBox(width: 28),
          const Text(
            'North sign',
            style: TextStyle(fontWeight: FontWeight.w600),
          ),
          const SizedBox(width: 9),
          const Text(
            '/  Sample project',
            style: TextStyle(color: muted, fontSize: 12),
          ),
        ],
        const Spacer(),
        IconButton(
          tooltip: m.dark ? 'Use light theme' : 'Use dark theme',
          onPressed: () => m.update(() => m.dark = !m.dark),
          icon: Icon(
            m.dark ? Icons.light_mode_outlined : Icons.dark_mode_outlined,
          ),
        ),
        IconButton(
          tooltip: 'About this prototype',
          onPressed: _about,
          icon: const Icon(Icons.help_outline),
        ),
        if (!tiny) ...[
          const SizedBox(width: 12),
          OutlinedButton.icon(
            onPressed: () {
              m.reset();
              transform.value = Matrix4.identity();
            },
            icon: const Icon(Icons.restart_alt, size: 16),
            label: const Text('Reset demo'),
          ),
        ],
      ],
    ),
  );

  Widget _workspaceBar(bool tiny) => Container(
    height: 54,
    decoration: BoxDecoration(
      color: panel,
      border: Border(bottom: BorderSide(color: Theme.of(context).dividerColor)),
    ),
    padding: EdgeInsets.symmetric(horizontal: tiny ? 8 : 20),
    child: Row(
      children: [
        if (!tiny) ...[
          IconButton(
            tooltip: 'Toggle layers panel',
            onPressed: () => m.update(() => m.layersOpen = !m.layersOpen),
            icon: const Icon(Icons.space_dashboard_outlined),
          ),
          const SizedBox(width: 16),
        ],
        for (final (i, title, icon) in [
          (0, 'Design', Icons.polyline_outlined),
          (1, 'Manufacture', Icons.precision_manufacturing_outlined),
          (2, 'Review', Icons.fact_check_outlined),
        ])
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: TextButton.icon(
              style: TextButton.styleFrom(
                foregroundColor: m.workspace == i ? teal : muted,
                backgroundColor: m.workspace == i
                    ? teal.withValues(alpha: .09)
                    : Colors.transparent,
                padding: EdgeInsets.symmetric(
                  horizontal: tiny ? 10 : 18,
                  vertical: 16,
                ),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(7),
                ),
              ),
              onPressed: () => m.update(() => m.workspace = i),
              icon: Icon(icon, size: 17),
              label: Text(
                title,
                style: const TextStyle(
                  fontWeight: FontWeight.w600,
                  fontSize: 12,
                ),
              ),
            ),
          ),
        const Spacer(),
        if (!tiny) ...[
          const Text(
            'UI evaluation',
            style: TextStyle(fontSize: 11, color: muted),
          ),
          const SizedBox(width: 18),
          FilledButton.icon(
            onPressed: () => m.update(() => m.workspace = 2),
            icon: const Icon(Icons.arrow_forward, size: 16),
            label: const Text('Review setup'),
          ),
        ],
      ],
    ),
  );

  Widget _sidebar() => Container(
    decoration: BoxDecoration(
      color: panel,
      border: Border(right: BorderSide(color: Theme.of(context).dividerColor)),
    ),
    child: _panelList(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      children: [
        section('PROJECT'),
        const Row(
          children: [
            Icon(Icons.folder_open, size: 18, color: teal),
            SizedBox(width: 9),
            Expanded(
              child: Text(
                'North dimensional sign',
                style: TextStyle(fontWeight: FontWeight.w600, fontSize: 12),
              ),
            ),
          ],
        ),
        const Padding(
          padding: EdgeInsets.only(left: 27, top: 7, bottom: 18),
          child: Text(
            'Sample artwork · inches',
            style: TextStyle(color: muted, fontSize: 11),
          ),
        ),
        const Divider(height: 1),
        section('PLATES'),
        for (var i = 0; i < 2; i++)
          Padding(
            padding: const EdgeInsets.only(bottom: 7),
            child: InkWell(
              key: ValueKey('plate-$i'),
              borderRadius: BorderRadius.circular(7),
              onTap: () {
                m.update(() => m.sheet = i);
                transform.value = Matrix4.identity();
              },
              child: Container(
                padding: const EdgeInsets.all(11),
                decoration: BoxDecoration(
                  color: m.sheet == i
                      ? teal.withValues(alpha: .08)
                      : Colors.transparent,
                  borderRadius: BorderRadius.circular(7),
                  border: Border.all(
                    color: m.sheet == i
                        ? teal.withValues(alpha: .25)
                        : Theme.of(context).dividerColor,
                  ),
                ),
                child: Row(
                  children: [
                    Icon(
                      Icons.crop_landscape,
                      size: 22,
                      color: m.sheet == i ? teal : muted,
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'Plate ${i + 1}',
                            style: const TextStyle(
                              fontWeight: FontWeight.w600,
                              fontSize: 12,
                            ),
                          ),
                          const SizedBox(height: 3),
                          Text(
                            i == 0
                                ? '96 × 48 in · Fronts'
                                : '96 × 48 in · Backs',
                            style: const TextStyle(fontSize: 10, color: muted),
                          ),
                        ],
                      ),
                    ),
                    if (m.sheet == i)
                      const Icon(Icons.check_circle, size: 14, color: teal),
                  ],
                ),
              ),
            ),
          ),
        section(m.workspace == 1 ? 'OPERATION ORDER' : 'LAYERS'),
        if (m.workspace != 1) ...[
          for (var i = 0; i < m.layers.length; i++) _layer(i),
          const SizedBox(height: 10),
          const Text(
            'Visibility affects this preview only.',
            style: TextStyle(fontSize: 10, color: muted),
          ),
        ] else ...[
          for (var i = 0; i < m.operations.length; i++)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: ListTile(
                contentPadding: const EdgeInsets.symmetric(horizontal: 8),
                dense: true,
                selected: m.selectedOperation == i,
                selectedTileColor: teal.withValues(alpha: .08),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(6),
                ),
                onTap: () => m.update(() => m.selectedOperation = i),
                leading: Text(
                  '0${i + 1}',
                  style: const TextStyle(color: muted, fontSize: 11),
                ),
                title: Text(
                  m.operations[i].name,
                  style: const TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                subtitle: Text(
                  m.operations[i].detail,
                  style: const TextStyle(fontSize: 10),
                ),
              ),
            ),
        ],
        section('QUICK NOTES'),
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: m.dark ? const Color(0xff293d3f) : const Color(0xfff4f6f3),
            borderRadius: BorderRadius.circular(8),
          ),
          child: const Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(Icons.tips_and_updates_outlined, size: 17, color: muted),
              SizedBox(height: 9),
              Text(
                'Room to focus.',
                style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
              ),
              SizedBox(height: 6),
              Text(
                'Select a letter to inspect it. Use the hand tool to pan, or scroll to zoom.',
                style: TextStyle(fontSize: 11, color: muted, height: 1.6),
              ),
            ],
          ),
        ),
      ],
    ),
  );

  Widget _layer(int i) {
    final layer = m.layers[i];
    return Row(
      children: [
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(color: layer.color, shape: BoxShape.circle),
        ),
        const SizedBox(width: 9),
        Expanded(
          child: Text(
            layer.name,
            style: TextStyle(fontSize: 11, color: layer.visible ? null : muted),
          ),
        ),
        Text(
          '${layer.count}',
          style: const TextStyle(fontSize: 10, color: muted),
        ),
        IconButton(
          key: ValueKey('layer-$i'),
          tooltip: '${layer.visible ? 'Hide' : 'Show'} ${layer.name}',
          onPressed: () => m.update(() {
            layer.visible = !layer.visible;
            if (i == 0 && !layer.visible) m.selected = -1;
          }),
          icon: Icon(
            layer.visible
                ? Icons.visibility_outlined
                : Icons.visibility_off_outlined,
            size: 16,
            color: muted,
          ),
        ),
      ],
    );
  }

  Widget _documentBar(bool tiny) => Container(
    height: 46,
    color: panel,
    padding: const EdgeInsets.symmetric(horizontal: 16),
    child: Row(
      children: [
        const Icon(Icons.description_outlined, size: 15, color: teal),
        const SizedBox(width: 8),
        const Text(
          'North sign.demo',
          style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
        ),
        const SizedBox(width: 10),
        const Icon(Icons.circle, size: 5, color: teal),
        const Spacer(),
        if (!tiny)
          Text(
            m.workspace == 2 ? 'Setup review' : 'Top view  /  XY',
            style: const TextStyle(fontSize: 10, color: muted),
          ),
      ],
    ),
  );

  Widget _canvas() => Container(
    color: m.dark ? const Color(0xff16262a) : const Color(0xffeaf0ed),
    child: Stack(
      children: [
        Positioned.fill(
          child: DrawingView(model: m, controller: transform),
        ),
        Positioned(
          left: 16,
          top: 16,
          child: Container(
            padding: const EdgeInsets.all(4),
            decoration: BoxDecoration(
              color: panel,
              borderRadius: BorderRadius.circular(9),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withValues(alpha: .04),
                  blurRadius: 12,
                ),
              ],
            ),
            child: Column(
              children: [
                _tool(
                  'Select geometry',
                  Icons.near_me_outlined,
                  !m.pan,
                  () => m.update(() => m.pan = false),
                ),
                _tool(
                  'Pan drawing',
                  Icons.pan_tool_outlined,
                  m.pan,
                  () => m.update(() => m.pan = true),
                ),
                const SizedBox(height: 8),
                _tool(
                  'Toggle grid',
                  Icons.grid_4x4,
                  m.grid,
                  () => m.update(() => m.grid = !m.grid),
                ),
                _tool(
                  'Toggle illustrative toolpaths',
                  Icons.route_outlined,
                  m.paths,
                  () => m.update(() => m.paths = !m.paths),
                ),
              ],
            ),
          ),
        ),
        Positioned(
          right: 16,
          top: 16,
          child: badge('SAMPLE GEOMETRY', color: muted),
        ),
        Positioned(
          bottom: 18,
          left: 18,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            decoration: BoxDecoration(
              color: panel,
              borderRadius: BorderRadius.circular(6),
            ),
            child: Text(
              m.selected < 0
                  ? 'Click a letter to inspect'
                  : 'Letter ${'NORTH'[m.selected]} selected',
              style: const TextStyle(fontSize: 11, color: muted),
            ),
          ),
        ),
        Positioned(
          bottom: 16,
          right: 16,
          child: Container(
            decoration: BoxDecoration(
              color: panel,
              borderRadius: BorderRadius.circular(7),
            ),
            child: Row(
              children: [
                IconButton(
                  tooltip: 'Zoom out',
                  onPressed: () => zoom(.8),
                  icon: const Icon(Icons.remove),
                ),
                ListenableBuilder(
                  listenable: transform,
                  builder: (context, _) => SizedBox(
                    width: 42,
                    child: Text(
                      '${(transform.value.getMaxScaleOnAxis() * 100).round()}%',
                      textAlign: TextAlign.center,
                      style: const TextStyle(fontSize: 10),
                    ),
                  ),
                ),
                IconButton(
                  tooltip: 'Zoom in',
                  onPressed: () => zoom(1.25),
                  icon: const Icon(Icons.add),
                ),
                IconButton(
                  tooltip: 'Fit plate',
                  onPressed: () => transform.value = Matrix4.identity(),
                  icon: const Icon(Icons.fit_screen),
                ),
              ],
            ),
          ),
        ),
      ],
    ),
  );

  Widget _tool(
    String title,
    IconData icon,
    bool selected,
    VoidCallback action,
  ) => IconButton(
    tooltip: title,
    onPressed: action,
    style: IconButton.styleFrom(
      backgroundColor: selected
          ? teal.withValues(alpha: .1)
          : Colors.transparent,
      foregroundColor: selected ? teal : muted,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
    ),
    icon: Icon(icon),
  );

  Widget _canvasFooter() => Container(
    height: 47,
    color: panel,
    padding: const EdgeInsets.symmetric(horizontal: 16),
    child: Row(
      children: [
        const Icon(Icons.layers_outlined, size: 15, color: muted),
        const SizedBox(width: 8),
        Text(
          'Plate ${m.sheet + 1}',
          style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w600),
        ),
        const SizedBox(width: 14),
        const Text('96 × 48 in', style: TextStyle(fontSize: 11, color: muted)),
        const Spacer(),
        Text(m.material, style: const TextStyle(fontSize: 11, color: muted)),
        const SizedBox(width: 12),
        badge('${m.thickness} in'),
      ],
    ),
  );

  Widget _inspector() => Container(
    decoration: BoxDecoration(
      color: panel,
      border: Border(left: BorderSide(color: Theme.of(context).dividerColor)),
    ),
    child: _panelList(
      padding: const EdgeInsets.fromLTRB(20, 20, 20, 24),
      children: [
        Row(
          children: [
            const Text(
              'Inspector',
              style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15),
            ),
            const Spacer(),
            Icon(
              m.workspace == 1 ? Icons.tune : Icons.tune_outlined,
              size: 18,
              color: muted,
            ),
          ],
        ),
        const SizedBox(height: 5),
        Text(
          m.workspace == 1 ? 'Operation settings' : 'Your setup at a glance',
          style: const TextStyle(color: muted, fontSize: 11),
        ),
        if (m.workspace != 1)
          ..._stockInspector()
        else
          ..._operationInspector(),
        const SizedBox(height: 22),
        const Divider(height: 1),
        section('PREVIEW'),
        SwitchListTile.adaptive(
          contentPadding: EdgeInsets.zero,
          dense: true,
          title: const Text('Toolpath overlay', style: TextStyle(fontSize: 12)),
          subtitle: const Text(
            'Illustration only',
            style: TextStyle(fontSize: 10, color: muted),
          ),
          value: m.paths,
          onChanged: (v) => m.update(() => m.paths = v),
        ),
        const SizedBox(height: 16),
        Container(
          padding: const EdgeInsets.all(13),
          decoration: BoxDecoration(
            border: Border.all(color: Theme.of(context).dividerColor),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              badge('EVALUATION BUILD', color: muted),
              const SizedBox(height: 10),
              const Text(
                'Try the workflow',
                style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
              ),
              const SizedBox(height: 6),
              const Text(
                'Explore the layout, selection, layers and setup. Changes stay in this session.',
                style: TextStyle(fontSize: 11, height: 1.6, color: muted),
              ),
            ],
          ),
        ),
      ],
    ),
  );

  List<Widget> _stockInspector() => [
    section('STOCK'),
    const Text('Material', style: TextStyle(fontSize: 11, color: muted)),
    const SizedBox(height: 7),
    DropdownButtonFormField<String>(
      key: ValueKey(m.material),
      initialValue: m.material,
      isExpanded: true,
      style: TextStyle(
        fontSize: 12,
        color: Theme.of(context).colorScheme.onSurface,
      ),
      items: [
        'Cast acrylic',
        'Aluminum',
        'PVC board',
      ].map((v) => DropdownMenuItem(value: v, child: Text(v))).toList(),
      onChanged: (v) => m.update(() => m.material = v!, invalidates: true),
    ),
    const SizedBox(height: 14),
    const Text('Thickness', style: TextStyle(fontSize: 11, color: muted)),
    const SizedBox(height: 7),
    DropdownButtonFormField<double>(
      key: ValueKey(m.thickness),
      initialValue: m.thickness,
      items: [.125, .25, .5, .75]
          .map(
            (v) => DropdownMenuItem(
              value: v,
              child: Text('$v in', style: const TextStyle(fontSize: 12)),
            ),
          )
          .toList(),
      onChanged: (v) => m.update(() => m.thickness = v!, invalidates: true),
    ),
    section('SELECTION'),
    if (m.selected < 0)
      Container(
        padding: const EdgeInsets.all(15),
        decoration: BoxDecoration(
          border: Border.all(color: Theme.of(context).dividerColor),
          borderRadius: BorderRadius.circular(7),
        ),
        child: const Column(
          children: [
            Icon(Icons.ads_click, size: 25, color: muted),
            SizedBox(height: 8),
            Text(
              'Select a letter',
              style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
            ),
            SizedBox(height: 5),
            Text(
              'Details appear here.',
              style: TextStyle(fontSize: 11, color: muted),
            ),
          ],
        ),
      )
    else ...[
      Text(
        'Letter ${'NORTH'[m.selected]}',
        style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w600),
      ),
      const SizedBox(height: 8),
      _property('Type', 'Closed vector'),
      _property('Layer', 'Letter outlines'),
      _property('Height', '17.56 in'),
      _property('Plate', '${m.sheet + 1}'),
      const SizedBox(height: 10),
      OutlinedButton(
        onPressed: () => m.update(() => m.selected = -1),
        child: const Text('Clear selection'),
      ),
    ],
  ];

  List<Widget> _operationInspector() => [
    section('ACTIVE OPERATION'),
    Row(
      children: [
        Icon(m.operations[m.selectedOperation].icon, color: teal, size: 23),
        const SizedBox(width: 10),
        Expanded(
          child: Text(
            m.operations[m.selectedOperation].name,
            style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
          ),
        ),
      ],
    ),
    const SizedBox(height: 8),
    Text(
      m.operations[m.selectedOperation].detail,
      style: const TextStyle(color: muted, fontSize: 12),
    ),
    SwitchListTile.adaptive(
      contentPadding: EdgeInsets.zero,
      title: const Text('Include operation', style: TextStyle(fontSize: 12)),
      value: m.operations[m.selectedOperation].enabled,
      onChanged: (v) => m.update(
        () => m.operations[m.selectedOperation].enabled = v,
        invalidates: true,
      ),
    ),
    section('DEMO PARAMETERS'),
    _property('Cut depth', '${m.depth.toStringAsFixed(3)} in'),
    Slider(
      value: m.depth,
      min: .025,
      max: .75,
      divisions: 29,
      label: '${m.depth.toStringAsFixed(3)} in',
      onChanged: (v) => m.update(() => m.depth = v, invalidates: true),
    ),
    _property('Feed rate', '${m.feed.round()} in/min'),
    Slider(
      value: m.feed,
      min: 30,
      max: 240,
      divisions: 21,
      label: '${m.feed.round()} in/min',
      onChanged: (v) => m.update(() => m.feed = v, invalidates: true),
    ),
    const Text(
      'Shared sample values demonstrate editing. No machining calculations run.',
      style: TextStyle(fontSize: 11, color: muted, height: 1.5),
    ),
    const SizedBox(height: 18),
    FilledButton.icon(
      onPressed: () => m.update(() => m.paths = !m.paths),
      icon: const Icon(Icons.route, size: 16),
      label: Text(m.paths ? 'Hide illustration' : 'Show illustration'),
    ),
  ];

  Widget _property(String title, String value) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 7),
    child: Row(
      children: [
        Expanded(
          child: Text(
            title,
            style: const TextStyle(color: muted, fontSize: 11),
          ),
        ),
        const SizedBox(width: 8),
        Flexible(
          child: Text(
            value,
            textAlign: TextAlign.right,
            style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 11),
          ),
        ),
      ],
    ),
  );

  Widget _panelList({
    required EdgeInsets padding,
    required List<Widget> children,
  }) => Material(
    color: panel,
    child: ListView(padding: padding, children: children),
  );

  Widget _review() => SingleChildScrollView(
    padding: const EdgeInsets.all(32),
    child: Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 650),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            badge('03 / REVIEW'),
            const SizedBox(height: 20),
            const Text(
              'A clear view before\nthe next step.',
              style: TextStyle(
                fontSize: 32,
                fontWeight: FontWeight.w600,
                height: 1.15,
                letterSpacing: -1,
              ),
            ),
            const SizedBox(height: 12),
            const Text(
              'An example of how setup review could feel in Workbench.',
              style: TextStyle(color: muted, height: 1.5),
            ),
            const SizedBox(height: 26),
            Container(
              padding: const EdgeInsets.all(20),
              decoration: BoxDecoration(
                color: panel,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Column(
                children: [
                  _property('Project', 'North dimensional sign'),
                  _property('Stock', '${m.material} · ${m.thickness} in'),
                  _property(
                    'Active plate',
                    'Plate ${m.sheet + 1} · 96 × 48 in',
                  ),
                  const Divider(),
                  for (final op in m.operations)
                    Material(
                      color: panel,
                      child: ListTile(
                        contentPadding: EdgeInsets.zero,
                        dense: true,
                        leading: Icon(
                          op.icon,
                          size: 20,
                          color: op.enabled ? teal : muted,
                        ),
                        title: Text(
                          op.name,
                          style: const TextStyle(fontSize: 12),
                        ),
                        trailing: badge(
                          op.enabled ? 'INCLUDED' : 'OFF',
                          color: op.enabled ? teal : muted,
                        ),
                      ),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 18),
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: const Color(0xffbd965c).withValues(alpha: .12),
                borderRadius: BorderRadius.circular(8),
              ),
              child: const Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(Icons.info_outline, color: Color(0xffa77b3e), size: 20),
                  SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      'Prototype review only. Toolpaths, collision checks and controller output are not connected.',
                      style: TextStyle(fontSize: 12, height: 1.5),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 22),
            Wrap(
              spacing: 12,
              runSpacing: 10,
              children: [
                FilledButton.icon(
                  onPressed: () => m.update(() => m.reviewed = !m.reviewed),
                  icon: Icon(
                    m.reviewed ? Icons.check : Icons.checklist,
                    size: 17,
                  ),
                  label: Text(
                    m.reviewed ? 'Demo review complete' : 'Mark demo reviewed',
                  ),
                ),
                OutlinedButton(
                  onPressed: () => m.update(() => m.workspace = 1),
                  child: const Text('Back to settings'),
                ),
              ],
            ),
          ],
        ),
      ),
    ),
  );

  Widget _status(bool tiny) => Container(
    height: 30,
    decoration: BoxDecoration(
      color: panel,
      border: Border(top: BorderSide(color: Theme.of(context).dividerColor)),
    ),
    padding: const EdgeInsets.symmetric(horizontal: 16),
    child: Row(
      children: [
        Container(
          width: 5,
          height: 5,
          decoration: const BoxDecoration(color: teal, shape: BoxShape.circle),
        ),
        const SizedBox(width: 7),
        const Text(
          'Local prototype',
          style: TextStyle(fontSize: 10, color: muted),
        ),
        if (!tiny) ...[
          const SizedBox(width: 18),
          Text(
            m.reviewed ? 'Demo reviewed' : 'No production connection',
            style: const TextStyle(fontSize: 10, color: muted),
          ),
        ],
        const Spacer(),
        const Text(
          'INCHES',
          style: TextStyle(fontSize: 9, letterSpacing: 1, color: muted),
        ),
        const SizedBox(width: 18),
        const Text(
          'Flutter · v0.1',
          style: TextStyle(fontSize: 10, color: muted),
        ),
      ],
    ),
  );

  void _about() => showDialog<void>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('A new surface for Workbench'),
      content: const SizedBox(
        width: 420,
        child: Text(
          'This standalone Flutter prototype explores a quieter desktop layout.\n\nTry selecting letters, toggling layers, switching plates, panning and zooming, changing stock, and reviewing operation settings.\n\nArtwork and toolpaths are illustrative. Nothing is loaded from or saved to your production jobs. Refreshing resets the session.\n\nAt narrow widths, side panels are hidden to prioritize the canvas. Use a desktop-sized window for the full evaluation.',
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Got it'),
        ),
      ],
    ),
  );
}
