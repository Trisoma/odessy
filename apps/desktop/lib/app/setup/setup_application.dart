import 'dart:convert';
import 'dart:ui' show AppExitResponse;
import 'package:flutter/material.dart';
import '../../features/setup/controllers/package_setup_controller.dart';
import '../../features/setup/controllers/maintenance_controller.dart';
import '../../features/setup/presentation/maintenance_page.dart';
import '../theme.dart';
import 'local_setup_backend.dart';
String? installationMode(List<String> args) => args.contains('--setup') ? 'setup' : args.contains('--manage') ? 'manage' : null;
class SetupApplication extends StatefulWidget {
  const SetupApplication({super.key,required this.mode, this.payloadRoot = '', this.installRoot = ''});
  final String mode, payloadRoot, installRoot;
  @override State<SetupApplication> createState()=>_SetupApplicationState();
}
class _SetupApplicationState extends State<SetupApplication> {
  late final c=PackageSetupController(LocalSetupBackend(payloadRoot:widget.payloadRoot,timeout:const Duration(minutes:30)));
  final maintenance=MaintenanceController(LocalSetupBackend());
  final location=TextEditingController(), model=TextEditingController(), endpoint=TextEditingController(text:'http://127.0.0.1:11434');
  int step=0; String role='combined', provider='later'; bool voice=false;
  BuildContext? dialogs;
  late final AppLifecycleListener lifecycle;
  @override void initState(){super.initState(); c.addListener(_refresh); lifecycle=AppLifecycleListener(onExitRequested:() async {
    if(c.busy && dialogs!=null){final yes=await showDialog<bool>(context:dialogs!,builder:(ctx)=>AlertDialog(title:const Text('Cancel and close setup?'),content:const Text('Reopen setup to inspect interrupted work. Nothing will be automatically repeated.'),actions:[TextButton(onPressed:()=>Navigator.pop(ctx,false),child:const Text('Keep open')),TextButton(onPressed:()=>Navigator.pop(ctx,true),child:const Text('Cancel and close'))]));if(yes!=true)return AppExitResponse.cancel;}
    await c.close();await maintenance.shutdown();return AppExitResponse.exit;
  }); _load();}
  void _refresh(){if(mounted)setState((){});}
  Future<void> _load() async {c.root=widget.installRoot;await c.run('context');if(!mounted)return;location.text=c.root;maintenance.update({'root':c.root,'bundle':c.context['bundle'],'config':c.context['config']});setState((){});}
  void _edit(){c.edit(destination:location.text,value:{'role':role,if(provider!='later'&&role!='execution')'model':{'provider':provider,'name':model.text,'endpoint':endpoint.text},'voice':{'enabled':voice&&role!='execution'}});}
  @override void dispose(){lifecycle.dispose();c.removeListener(_refresh);c.close();maintenance.dispose();location.dispose();model.dispose();endpoint.dispose();super.dispose();}
  Widget field(TextEditingController controller,String label)=>Padding(padding:const EdgeInsets.symmetric(vertical:8),child:TextField(controller:controller,enabled:!c.busy,onChanged:(_)=>_edit(),decoration:InputDecoration(labelText:label)));
  Widget body(){if(c.context.isEmpty)return Text(c.error.isEmpty?'Verifying installer payload…':c.error);
    if(widget.mode=='manage')return MaintenancePage(controller:maintenance);
    return switch(step){
      0=>const Column(crossAxisAlignment:CrossAxisAlignment.start,children:[Text('Welcome to Odessy'),SizedBox(height:16),Text('Install your local assistant. Choose your role and model. No model is downloaded or run without your consent.')]),
      1=>Column(children:[DropdownButtonFormField<String>(initialValue:role,items:const [DropdownMenuItem(value:'combined',child:Text('This computer: coordinator and tools')),DropdownMenuItem(value:'coordinator',child:Text('Coordinator only')),DropdownMenuItem(value:'execution',child:Text('Execution node (headless)'))],onChanged:c.busy?null:(v){role=v!;_edit();}),field(location,'Installation location')]),
      2=>ListView(children:[if(role!='execution')...[
        DropdownButtonFormField<String>(initialValue:provider,items:const [DropdownMenuItem(value:'later',child:Text('Configure model later')),DropdownMenuItem(value:'ollama',child:Text('Existing Ollama model')),DropdownMenuItem(value:'openai',child:Text('Compatible remote endpoint'))],onChanged:(v){provider=v!;_edit();}),
        if(provider!='later')... [field(model,'Existing model name'),field(endpoint,'Model endpoint')],
        SwitchListTile(title:const Text('Enable voice'),subtitle:const Text('Optional voice assets must already be configured; no download is implied.'),value:voice,onChanged:(v){voice=v;_edit();})] else const Text('Execution nodes do not need a model or desktop at runtime.')]),
      3=>ListView(children:[Text('Install $role at ${c.root}'),const Text('Model downloads: none. Inference: none.'),SelectableText(const JsonEncoder.withIndent('  ').convert(c.review)),if(c.review.isEmpty)FilledButton(onPressed:c.busy?null:()=>c.run('package-review'),child:const Text('Review installation'))]),
      4=>ListView(children:[Text(c.status),if(c.busy)const LinearProgressIndicator(),if(c.error.isNotEmpty)SelectableText(c.error),if(c.interrupted)FilledButton(onPressed:c.busy?null:(){step=3;c.run('package-review');},child:const Text('Inspect and review recovery')),if(c.busy)TextButton(onPressed:c.cancel,child:const Text('Cancel'))]),
      _=>Column(children:[const Text('Odessy is installed'),Text(c.root),if(role=='execution')const Text('Open pairing management to authorize a coordinator. No model was started.') else FilledButton(onPressed:c.busy?null:()=>c.run('package-launch'),child:const Text('Launch Odessy')),if(c.error.isNotEmpty)SelectableText(c.error),TextButton(onPressed:()=>setState(()=>step=3),child:const Text('Review installation details'))])};}
  @override Widget build(BuildContext context)=>MaterialApp(title:'Odessy Setup',theme:odessyTheme(Brightness.dark),home:Builder(builder:(ctx){dialogs=ctx;if(c.installed&&step==4)step=5;return Scaffold(appBar:AppBar(title:Text(widget.mode=='manage'?'Manage Odessy installation':'Odessy Setup')),body:Padding(padding:const EdgeInsets.all(32),child:Column(crossAxisAlignment:CrossAxisAlignment.stretch,children:[if(widget.mode!='manage')Text('${step+1} of 6 · ${['Welcome','Role and location','Model and voice','Review','Installation','Finish'][step]}'),const SizedBox(height:24),Expanded(child:body()),if(c.error.isNotEmpty&&step!=4&&step!=5)Text(c.error),if(widget.mode!='manage'&&step<4)Row(children:[TextButton(onPressed:step==0||c.busy?null:()=>setState(()=>step--),child:const Text('Back')),const Spacer(),FilledButton(onPressed:c.busy||c.context.isEmpty||(step==3&&c.review.isEmpty)?null:()async{if(step==3){setState(()=>step=4);await c.run('package-install');}else{setState(()=>step++);}},child:Text(step==3?'Install':'Continue'))])]))); }));
}

