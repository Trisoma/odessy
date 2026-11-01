import 'package:test/test.dart';
import 'package:odessy_client/odessy_client.dart';

void main(){
  test('setup error categories are optional and forward compatible',(){
    SetupReply reply(Map<String,dynamic> extra)=>SetupReply.fromJson({'id':'test','status':'error','reason':'A safe explanation',...extra},'test');
    expect(reply({}).code,isNull);
    expect(reply({'code':'credentials'}).code,'credentials');
    expect(reply({'code':'future_category'}).code,'future_category');
    expect(reply({'code':'unsafe\ntext'}).code,isNull);
    expect(reply({'code':'credentials'}).reason,'A safe explanation');
  });
}
