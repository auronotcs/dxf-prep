import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/mailer_dialog.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'design_workspace_test.dart' show TestDesignController;

class MailerModel extends TestDesignController {
  final Json fixture = jsonDecode(
    File('test/fixtures/mailer_authoring.json').readAsStringSync(),
  );
  final requests = <Json>[];
  Json? applied;
  bool rejectRequests = false;
  bool warning = false;
  MailerModel() {
    state['mailerAuthoring'] = true;
  }
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    requests.add({'command': command, 'params': params});
    if (command == 'design.mailer_begin') {
      return {'ok': true, 'context': fixture['context']};
    }
    if (rejectRequests) {
      return {
        'ok': false,
        'error': 'Drawing changed. Reopen the mailer review.',
      };
    }
    return {
      'ok': true,
      'preview':
          fixture[warning
              ? 'warning'
              : params['options']['rotate'] == true
              ? 'rotated'
              : 'normal'],
    };
  }

  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    commands.add(command);
    applied = params;
    if (rejectRequests) {
      message = 'Drawing changed. Reopen the mailer review.';
      return false;
    }
    return true;
  }

  void externalChange() {
    state['revision'] = (state['revision'] as int) + 1;
    notifyListeners();
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
    await (FontLoader('Roboto')..addFont(
          File(
            'C:/Windows/Fonts/segoeui.ttf',
          ).readAsBytes().then(ByteData.sublistView),
        ))
        .load();
  });
  Future<void> setupSize(
    WidgetTester t, [
    Size size = const Size(800, 600),
  ]) async {
    t.view.physicalSize = size;
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
  }

  Future<void> mount(WidgetTester t, MailerModel m) async {
    await setupSize(t);
    addTearDown(m.dispose);
    await t.pumpWidget(
      RepaintBoundary(
        key: const ValueKey('mailer-image'),
        child: MaterialApp(
          home: Builder(
            builder: (context) => Scaffold(
              body: TextButton(
                onPressed: () => showDialog<bool>(
                  context: context,
                  builder: (_) => MailerDialog(model: m),
                ),
                child: const Text('Start mailer'),
              ),
            ),
          ),
        ),
      ),
    );
    await t.tap(find.text('Start mailer'));
    await t.pumpAndSettle();
  }

  Future<void> click(WidgetTester t, String label) async {
    final f = find.text(label);
    await t.ensureVisible(f);
    await t.pumpAndSettle();
    await t.tap(f);
    await t.pumpAndSettle();
  }

  Future<void> enter(WidgetTester t, String label, String value) async {
    final f = find.byWidgetPredicate(
      (w) => w is TextField && w.decoration?.labelText == label,
    );
    await t.ensureVisible(f);
    await t.enterText(f, value);
    await t.pumpAndSettle();
  }

  bool canApply(WidgetTester t) =>
      t
          .widget<FilledButton>(
            find.widgetWithText(FilledButton, 'Create box geometry'),
          )
          .onPressed !=
      null;

  const ack =
      'I will test fold fit and review score depth, hold-down and cutter clearance';
  const placement =
      'I reviewed the placement findings; add geometry for layout review';
  testWidgets(
    'Physical mailer preview, rotation, compact view and cancel preserve source',
    (t) async {
      final m = MailerModel();
      final before = jsonEncode(m.state);
      await mount(t, m);
      expect(find.byKey(const ValueKey('mailer-preview')), findsOneWidget);
      expect(find.textContaining('1 closed cut outline'), findsOneWidget);
      expect(find.textContaining('12 open score lines'), findsOneWidget);
      expect(canApply(t), false);
      await click(t, ack);
      expect(canApply(t), true);
      await click(t, 'Rotate blank 90°');
      expect(canApply(t), false);
      expect(find.byKey(const ValueKey('mailer-preview')), findsNothing);
      await enter(t, 'Base length, fold to fold', '12');
      await click(t, 'Preview blank');
      expect(m.requests.last['params']['options']['length'], 12);
      expect(m.requests.last['params']['options']['rotate'], true);
      expect(canApply(t), false);
      await t.ensureVisible(find.byKey(const ValueKey('mailer-preview')));
      await t.pumpAndSettle();
      final boundary = t.renderObject<RenderRepaintBoundary>(
        find.byKey(const ValueKey('mailer-image')),
      );
      await t.runAsync(() async {
        final image = await boundary.toImage();
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        File(
          'build/ui-review/mailer-compact.png',
        ).writeAsBytesSync(bytes!.buffer.asUint8List());
        image.dispose();
      });
      await click(t, 'Cancel');
      expect(find.byType(MailerDialog), findsNothing);
      expect(jsonEncode(m.state), before);
      expect(m.commands, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets('Placement findings and invalid or stale drafts gate Apply', (
    t,
  ) async {
    final m = MailerModel()..warning = true;
    await mount(t, m);
    await click(t, ack);
    expect(canApply(t), false);
    await click(t, placement);
    expect(canApply(t), true);
    await enter(t, 'Cardboard thickness', 'NaN');
    final count = m.requests.length;
    await click(t, 'Preview blank');
    expect(m.requests.length, count);
    expect(canApply(t), false);
    await enter(t, 'Cardboard thickness', '.125');
    await click(t, 'Preview blank');
    await click(t, ack);
    await click(t, placement);
    m.rejectRequests = true;
    await click(t, 'Create box geometry');
    expect(canApply(t), false);
    expect(find.textContaining('Drawing changed'), findsOneWidget);
    m.rejectRequests = false;
    await click(t, 'Preview blank');
    m.externalChange();
    await t.pumpAndSettle();
    expect(canApply(t), false);
    expect(
      find.text('Drawing changed. Close and reopen the mailer review.'),
      findsOneWidget,
    );
    expect(t.takeException(), isNull);
  });
  testWidgets('Apply sends the current exact token and explicit reviews', (
    t,
  ) async {
    final m = MailerModel();
    await mount(t, m);
    await click(t, ack);
    await click(t, 'Create box geometry');
    expect(m.commands, ['design.mailer_apply']);
    expect(m.applied, {
      'token': m.fixture['normal']['token'],
      'reviewed': true,
      'placement_reviewed': false,
    });
    expect(find.byType(MailerDialog), findsNothing);
    expect(t.takeException(), isNull);
  });
  testWidgets(
    'Macro works with empty selection and cancel retains view; old backend is gated',
    (t) async {
      await setupSize(t, const Size(1400, 900));
      final m = MailerModel();
      addTearDown(m.dispose);
      await t.pumpWidget(DesignApp(controller: m, startBackend: false));
      await t.pumpAndSettle();
      final dynamic w = t.state(find.byType(DesignWorkspace));
      w.view.scale = 32.0;
      w.view.translation = const Offset(120, 450);
      w.view.grid = true;
      final task = w.action('mailerAuthor') as Future<void>;
      await t.pumpAndSettle();
      expect(find.byType(MailerDialog), findsOneWidget);
      await click(t, 'Cancel');
      await task;
      expect(w.view.scale, 32);
      expect(w.view.translation, const Offset(120, 450));
      expect(w.view.grid, true);
      expect(m.selection, isEmpty);
      expect(m.commands, isEmpty);
      m.state['mailerAuthoring'] = false;
      await w.action('mailerAuthor');
      await t.pumpAndSettle();
      expect(find.byType(MailerDialog), findsNothing);
      expect(t.takeException(), isNull);
    },
  );
}
