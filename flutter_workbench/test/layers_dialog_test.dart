import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/layers_dialog.dart';

class LayerModel extends DesignController {
  final commands = <String>[];
  LayerModel() {
    ready = true;
    busy = false;
    state = {
      'capabilities': ['delete_layer'],
      'layers': <Json>[],
      'entities': <Json>[],
    };
  }
  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    commands.add(command);
    final list = layers.toList();
    if (command == 'layer.set') {
      list.add({
        'name': params['name'],
        'objects': 0,
        'visible': true,
        'locked': false,
      });
    }
    if (command == 'design.delete_layer') {
      list.removeWhere((l) => l['name'] == params['name']);
    }
    state['layers'] = list;
    notifyListeners();
    return true;
  }
}

void main() {
  testWidgets(
    'Layer manager adds a trimmed layer, rejects duplicates, and deletes with confirmation',
    (tester) async {
      final model = LayerModel();
      addTearDown(model.dispose);
      var current = '0';
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: LayersDialog(
              model: model,
              current: current,
              onCurrent: (name) => current = name,
            ),
          ),
        ),
      );
      expect(find.text('0 (Default)'), findsOneWidget);
      expect(
        tester
            .widget<TextButton>(find.widgetWithText(TextButton, 'Delete'))
            .onPressed,
        isNull,
      );
      await tester.tap(find.text('Add layer'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), '  Cut  ');
      await tester.tap(find.text('Create layer'));
      await tester.pumpAndSettle();
      expect(current, 'Cut');
      expect(find.text('Cut'), findsOneWidget);
      await tester.tap(find.text('Add layer'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), 'Cut');
      await tester.tap(find.text('Create layer'));
      await tester.pumpAndSettle();
      expect(find.text('That layer already exists.'), findsOneWidget);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Delete').last);
      await tester.pumpAndSettle();
      expect(model.commands, ['layer.set']);
      await tester.tap(find.text('Delete layer'));
      await tester.pumpAndSettle();
      expect(current, '0');
      expect(find.text('Cut'), findsNothing);
      expect(model.commands, ['layer.set', 'design.delete_layer']);
    },
  );

  testWidgets('Old backends explain restart requirement and disable deletion', (
    tester,
  ) async {
    final model = LayerModel()..state['capabilities'] = <String>[];
    model.state['layers'] = <Json>[
      {'name': 'Cut', 'objects': 2},
    ];
    addTearDown(model.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: LayersDialog(model: model, current: '0', onCurrent: (_) {}),
        ),
      ),
    );
    expect(find.textContaining('restart development mode'), findsOneWidget);
    for (final button in tester.widgetList<TextButton>(
      find.widgetWithText(TextButton, 'Delete'),
    )) {
      expect(button.onPressed, isNull);
    }
  });
}
