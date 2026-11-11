import 'package:flutter/material.dart';
import 'controller.dart';
import 'stock_material.dart';

class ManufacturingRibbon extends StatelessWidget {
  const ManufacturingRibbon({
    super.key,
    required this.model,
    required this.enabled,
    required this.actions,
  });
  final DesignController model;
  final bool enabled;
  final Map<String, VoidCallback?> actions;
  Json? get activePlate =>
      model.plates
          .where((p) => p['number'] == model.state['activePlate'])
          .firstOrNull ??
      model.plates.firstOrNull;
  Widget button(String title, IconData icon, String action) => SizedBox(
    width: 74,
    height: 54,
    child: TextButton(
      onPressed: enabled ? actions[action] : null,
      style: TextButton.styleFrom(padding: EdgeInsets.zero),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          SizedBox(height: 22, child: Icon(icon, size: 21)),
          const SizedBox(height: 3),
          SizedBox(
            height: 16,
            child: FittedBox(
              child: Text(
                title,
                maxLines: 1,
                style: const TextStyle(fontSize: 11, height: 1),
              ),
            ),
          ),
        ],
      ),
    ),
  );
  Widget group(String label, List<Widget> children) => Container(
    margin: const EdgeInsets.only(right: 5),
    padding: const EdgeInsets.symmetric(horizontal: 3),
    decoration: BoxDecoration(
      border: Border(right: BorderSide(color: Colors.grey.shade400)),
    ),
    child: Row(
      children: [
        RotatedBox(
          quarterTurns: 3,
          child: Text(label, style: const TextStyle(fontSize: 9)),
        ),
        ...children,
      ],
    ),
  );
  @override
  Widget build(BuildContext context) => Material(
    color: Theme.of(context).colorScheme.surfaceContainerLow,
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SizedBox(
          height: 62,
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                group('FILE', [button('Save', Icons.save_outlined, 'save')]),
                group('SETUP', [
                  button('Job', Icons.settings_outlined, 'stock'),
                  button('Readiness', Icons.fact_check_outlined, 'readiness'),
                  button('Library', Icons.menu_book_outlined, 'library'),
                  button('Turret', Icons.apps, 'turret'),
                  button('Keepouts', Icons.fence, 'keepouts'),
                ]),
                group('CREATE', [
                  button('Outside', Icons.crop_square, 'outside'),
                  button('Inside', Icons.filter_center_focus, 'inside'),
                  button('Cut Line', Icons.horizontal_rule, 'line'),
                  button('Cleanout', Icons.layers_outlined, 'fill'),
                  button('Drill', Icons.control_point, 'drill'),
                  button('Engrave', Icons.edit_outlined, 'engrave'),
                  button('More', Icons.more_horiz, 'special'),
                ]),
                group('EFFICIENCY', [
                  button('Motion', Icons.speed, 'optimization'),
                ]),
                group('INSPECT', [
                  button('Pick', Icons.ads_click, 'pick'),
                  button('Inspect', Icons.fact_check_outlined, 'inspect'),
                ]),
                group('OUTPUT', [
                  button('Generate', Icons.play_arrow, 'generate'),
                  button('Output', Icons.output, 'output'),
                ]),
              ],
            ),
          ),
        ),
        const Divider(height: 1),
        SizedBox(
          height: 40,
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                const SizedBox(width: 8),
                const Text(
                  'VIEW',
                  style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold),
                ),
                TextButton(
                  onPressed: enabled ? actions['stock'] : null,
                  child: Text(
                    'Stock ${activePlate == null ? '—' : '${activePlate!['width']} × ${activePlate!['height']}'}',
                  ),
                ),
                StockMaterialControls(model: model),
                const SizedBox(width: 12),
                const Text(
                  'Active plate: ',
                  style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold),
                ),
                DropdownButton<int>(
                  isDense: true,
                  value: activePlate?['number'] as int?,
                  items: [
                    for (final p in model.plates)
                      DropdownMenuItem(
                        value: p['number'] as int,
                        child: Text('${p['number']} · ${p['name'] ?? 'Plate'}'),
                      ),
                  ],
                  onChanged: enabled
                      ? (v) {
                          if (v != null) {
                            model.run('plate.set_active', {'number': v});
                          }
                        }
                      : null,
                ),
                const SizedBox(width: 12),
                Text('Units: ${model.units}'),
              ],
            ),
          ),
        ),
        const Divider(height: 1),
      ],
    ),
  );
}
