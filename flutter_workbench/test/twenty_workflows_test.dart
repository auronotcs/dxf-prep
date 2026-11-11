import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/repeat_dialog.dart';
import 'package:workbench_prototype/design/closed_overruns_dialog.dart';
import 'package:workbench_prototype/design/assembly_review_dialog.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'manufacturing_expanded_test.dart' show ExpandedModel;

class BatchModel extends ExpandedModel {
  Json? reviewOptions;
  String? reviewCommand;
  Json overrun = {
    'token': 'source',
    'units': 'in',
    'limit': .125,
    'rows': <Json>[
      {
        'id': 'outline',
        'layer': '0',
        'bounds': [-.1, 2, 8, 6],
        'choices': [
          {
            'number': 1,
            'edges': [.1, 0, 0, 0],
          },
        ],
        'owner': null,
        'saved': null,
        'stale': false,
        'editable': true,
        'operations': ['Outside'],
      },
    ],
  };
  Json assembly = {
    'token': 'source',
    'units': 'in',
    'rows': <Json>[
      {
        'id': 'pan',
        'name': 'Applied pan',
        'error': null,
        'targets': {
          'outline': ['pan'],
          'marks': ['mark'],
          'holes': ['hole'],
        },
        'editable': {'outline': true, 'marks': true, 'holes': true},
      },
    ],
  };
  BatchModel() {
    state['stepRepeat'] = true;
    state['assemblyReview'] = true;
    state['activePlate'] = 1;
    state['plates'] = <Json>[
      {'number': 1, 'x': 0.0, 'y': 0.0, 'width': 96.0, 'height': 48.0},
    ];
    state['manufacturing']['holeExclusions'] = true;
    state['manufacturing']['keepoutsEditable'] = true;
    state['manufacturing']['keepouts'] = <Json>[];
  }
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    reviewCommand = command;
    reviewOptions = params;
    if (command == 'design.repeat_preview') {
      return {
        'ok': true,
        'preview': {
          'token': 'repeat',
          'count': 1,
          'boxes': [
            [2, 2, 6, 4],
            [6.25, 2, 10.25, 4],
          ],
          'overlap': [],
          'outside': [],
          'warnings': [],
          'plate': plates.first,
          'geometry': entities,
        },
      };
    }
    if (command == 'manufacturing.closed_overruns') {
      return {'ok': true, 'manufacturing': overrun};
    }
    if (command == 'design.pan_review' || command == 'design.cabinet_review') {
      return {'ok': true, 'review': assembly};
    }
    return super.request(command, params);
  }
}

void main() {
  setUpAll(() async {
    await (FontLoader('MaterialIcons')..addFont(
          File(
            '../.flutter-sdk/bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf',
          ).readAsBytes().then(ByteData.sublistView),
        ))
        .load();
    for (final name in ['Roboto', 'Segoe UI']) {
      await (FontLoader(name)..addFont(
            File(
              'C:/Windows/Fonts/segoeui.ttf',
            ).readAsBytes().then(ByteData.sublistView),
          ))
          .load();
    }
  });
  Future<void> mount(
    WidgetTester t,
    Widget child, {
    bool compact = false,
  }) async {
    t.view.physicalSize = compact
        ? const Size(800, 600)
        : const Size(1200, 950);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    await t.pumpWidget(
      RepaintBoundary(
        key: const ValueKey('batch-image'),
        child: MaterialApp(home: Scaffold(body: child)),
      ),
    );
    await t.pumpAndSettle();
  }

  Future<void> click(WidgetTester t, String label) async {
    final f = find.text(label);
    await t.ensureVisible(f);
    await t.pumpAndSettle();
    await t.tap(f);
    await t.pumpAndSettle();
  }

  Finder input(String label) => find.byWidgetPredicate(
    (w) => w is TextField && w.decoration?.labelText == label,
  );
  Future<void> capture(WidgetTester t, String name) async {
    final boundary = t.renderObject<RenderRepaintBoundary>(
      find.byKey(const ValueKey('batch-image')),
    );
    await t.runAsync(() async {
      final image = await boundary.toImage();
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      Directory('build/ui-review').createSync(recursive: true);
      File(
        'build/ui-review/$name.png',
      ).writeAsBytesSync(bytes!.buffer.asUint8List());
      image.dispose();
    });
  }

  testWidgets(
    'Repeat invalidation, reviewed apply, compact view and preserved document',
    (t) async {
      final m = BatchModel();
      addTearDown(m.dispose);
      final before = jsonEncode(m.state);
      await mount(t, RepeatDialog(model: m), compact: true);
      await click(t, 'Preview repeat');
      expect(m.reviewOptions?['spacing'], 'gap');
      expect(jsonEncode(m.state), before);
      expect(
        t
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Apply repeat'),
            )
            .onPressed,
        isNull,
      );
      await capture(t, 'repeat-compact');
      await t.enterText(input('Horizontal spacing'), '0.5');
      await t.pumpAndSettle();
      expect(find.byType(CheckboxListTile), findsNothing);
      await click(t, 'Preview repeat');
      await click(t, 'I reviewed placement, overlaps and stock boundaries.');
      await click(t, 'Apply repeat');
      expect(m.calls.last, 'design.repeat_apply');
      expect(m.applied?['reviewed'], true);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets('Repeat rejects invalid numbers and stale source', (t) async {
    final m = BatchModel();
    addTearDown(m.dispose);
    await mount(t, RepeatDialog(model: m));
    await t.enterText(input('Columns'), '500');
    await click(t, 'Preview repeat');
    expect(m.reviewCommand, isNull);
    await t.enterText(input('Columns'), '2');
    await click(t, 'Preview repeat');
    await m.run('selection.clear');
    await t.pumpAndSettle();
    expect(
      find.text('Drawing changed. Reopen Step and Repeat.'),
      findsOneWidget,
    );
    expect(
      t
          .widget<OutlinedButton>(
            find.widgetWithText(OutlinedButton, 'Preview repeat'),
          )
          .onPressed,
      isNull,
    );
  });
  testWidgets(
    'Closed outline requires explicit approval and keeps source token',
    (t) async {
      final m = BatchModel();
      addTearDown(m.dispose);
      await mount(t, ClosedOverrunsDialog(model: m), compact: true);
      await click(t, 'Plate 1');
      await click(t, 'Review approval');
      expect(m.calls, isEmpty);
      await click(t, 'Cancel');
      expect(m.calls, isEmpty);
      await click(t, 'Review approval');
      await click(t, 'Confirm');
      expect(m.calls.last, 'manufacturing.closed_overrun_assign');
      expect(m.applied, {
        'id': 'outline',
        'plate_number': 1,
        'token': 'source',
        'reviewed': true,
      });
      expect(t.takeException(), isNull);
    },
  );
  testWidgets('DR pan feature handoff selects only reviewed marks', (t) async {
    final m = BatchModel();
    addTearDown(m.dispose);
    await mount(t, AssemblyReviewDialog(model: m, cabinet: false));
    await click(t, 'Select marks → Engrave (1)');
    expect(m.calls.last, 'design.pan_select');
    expect(m.applied?['kind'], 'marks');
    expect(m.applied?['token'], 'source');
  });
  testWidgets('Cabinet registration inspection cannot apply or fabricate', (
    t,
  ) async {
    final m = BatchModel();
    addTearDown(m.dispose);
    m.assembly = {
      'token': 'cabinet',
      'units': 'in',
      'rows': [
        {
          'id': 'cabinet',
          'valid': false,
          'error': 'Back/hole geometry changed',
          'planes': [],
          'tabs': [],
          'geometry': [],
        },
      ],
    };
    await mount(
      t,
      AssemblyReviewDialog(model: m, cabinet: true),
      compact: true,
    );
    expect(find.text('Back/hole geometry changed'), findsOneWidget);
    expect(find.byType(FilledButton), findsNothing);
    expect(m.calls, isEmpty);
    expect(t.takeException(), isNull);
  });
  testWidgets(
    'Hole editor validates margin and sends explicit kind and plate',
    (t) async {
      final m = BatchModel();
      addTearDown(m.dispose);
      m.response = {'status': 'idle'};
      await mount(
        t,
        ManufacturingWorkspace(model: m, onSave: () async => true),
      );
      await click(t, 'Keepouts');
      await click(t, 'Add hole / damaged area');
      await t.enterText(input('Extra exclusion margin'), '-1');
      await click(t, 'Review keepout');
      expect(find.text('Must be nonnegative.'), findsOneWidget);
      await t.enterText(input('Extra exclusion margin'), '0.25');
      await click(t, 'Review keepout');
      await t.pump(const Duration(milliseconds: 400));
      await t.pumpAndSettle();
      expect(m.requested?['params']['kind'], 'hole_exclusion');
      expect(m.requested?['params']['extra_margin'], .25);
      expect(m.requested?['params']['plate_number'], 1);
      expect(t.takeException(), isNull);
    },
  );
}
