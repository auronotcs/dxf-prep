import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'design_workspace_test.dart' show TestDesignController;

class TabModel extends TestDesignController {
  bool failOpen = false, shut = false;
  @override
  Future<void> start() async {}
  @override
  Future<void> shutdown() async {
    shut = true;
  }

  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    commands.add(command);
    if (failOpen && command == 'project.open') {
      message = 'Invalid drawing';
      return false;
    }
    if (command == 'project.new') state['name'] = params['name'];
    if (command == 'project.save') return false;
    notifyListeners();
    return true;
  }
}

void main() {
  Future<dynamic> mount(
    WidgetTester t,
    TabModel first,
    TabModel Function() factory,
  ) async {
    t.view.physicalSize = const Size(1400, 900);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    addTearDown(first.dispose);
    await t.pumpWidget(
      DesignApp(
        controller: first,
        startBackend: false,
        controllerFactory: factory,
      ),
    );
    await t.pumpAndSettle();
    return t.state(find.byType(DocumentTabs));
  }

  testWidgets(
    'Tabs retain workspace view, selection and controller history; failed open keeps current tabs',
    (t) async {
      final a = TabModel(),
          b = TabModel(),
          failed = TabModel()..failOpen = true;
      var count = 0;
      final dynamic host = await mount(t, a, () => count++ == 0 ? b : failed);
      final dynamic first = t.state(find.byType(DesignWorkspace));
      first.view.scale = 17.0;
      a.state['selected'] = ['a'];
      a.state['canUndo'] = true;
      final next =
          host.openDocument('project.new', {'name': 'Second'}) as Future<void>;
      await t.pumpAndSettle();
      await next;
      expect(host.models.length, 2);
      expect(host.active, 1);
      expect(b.state['canUndo'], isNull);
      host.setState(() {
        host.active = 0;
      });
      await t.pumpAndSettle();
      expect(identical(t.state(find.byType(DesignWorkspace)), first), true);
      expect(first.view.scale, 17);
      expect(a.selection, {'a'});
      expect(a.state['canUndo'], true);
      final bad =
          host.openDocument('project.open', {'path': 'bad'}) as Future<void>;
      await t.pumpAndSettle();
      await bad;
      expect(host.models.length, 2);
      expect(host.active, 0);
      expect(a.message, 'Invalid drawing');
      expect(failed.shut, true);
      expect(t.takeException(), isNull);
    },
  );

  testWidgets(
    'Cancel second window-close decision leaves all tabs and recovery intact',
    (t) async {
      final a = TabModel(), b = TabModel();
      final dynamic host = await mount(t, a, () => b);
      final next =
          host.openDocument('project.new', {'name': 'Second'}) as Future<void>;
      await t.pumpAndSettle();
      await next;
      for (final m in [a, b]) {
        m.state['dirty'] = true;
        m.state['recovery'] = {'enabled': true};
        m.commands.clear();
      }
      final closing = host.closeWindow() as Future<void>;
      await t.pumpAndSettle();
      await t.tap(find.text('Discard'));
      await t.pumpAndSettle();
      expect(find.textContaining('Second?'), findsOneWidget);
      await t.tap(find.text('Cancel'));
      await t.pumpAndSettle();
      await closing;
      expect(host.models.length, 2);
      expect(a.shut || b.shut, false);
      expect(a.commands, isEmpty);
      expect(b.commands, isEmpty);
      expect(a.dirty && b.dirty, true);
    },
  );

  testWidgets(
    'Failed save prevents tab close; explicit discard closes only that session',
    (t) async {
      final a = TabModel(), b = TabModel();
      t.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        const MethodChannel('workbench/design'),
        (call) async => 'bad-save.camjob.json',
      );
      addTearDown(
        () => t.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          const MethodChannel('workbench/design'),
          null,
        ),
      );
      final dynamic host = await mount(t, a, () => b);
      final next =
          host.openDocument('project.new', {'name': 'Second'}) as Future<void>;
      await t.pumpAndSettle();
      await next;
      b.state['dirty'] = true;
      b.state['recovery'] = {'enabled': true};
      final close = host.closeTab(1) as Future<void>;
      await t.pumpAndSettle();
      await t.tap(find.text('Save As'));
      await t.pumpAndSettle();
      await close;
      expect(host.models.length, 2);
      expect(b.shut, false);
      final discard = host.closeTab(1) as Future<void>;
      await t.pumpAndSettle();
      await t.tap(find.text('Discard'));
      await t.pumpAndSettle();
      await discard;
      expect(host.models.length, 1);
      expect(a.shut, false);
      expect(b.shut, true);
      expect(b.commands, contains('recovery.clear'));
      expect(t.takeException(), isNull);
    },
  );
}
