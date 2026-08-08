package com.c21rocket.app;

import android.Manifest;
import android.app.Activity;
import android.bluetooth.*;
import android.bluetooth.le.*;
import android.content.pm.PackageManager;
import android.os.*;
import android.view.Gravity;
import android.widget.*;
import java.util.*;

public class MainActivity extends Activity {
    private static final UUID SERVICE = UUID.fromString("00010203-0405-0607-0809-0a0b0c0dffe0");
    private static final UUID WRITE = UUID.fromString("00010203-0405-0607-0809-0a0b0c0dffe2");
    private static final byte[] ROCKET_ON = hex("4664AA0127802E");
    private static final byte[] ROCKET_OFF = hex("4664AA012700AE");
    private TextView status; private BluetoothLeScanner scanner; private BluetoothGatt gatt; private byte[] pending;
    private final Handler h = new Handler(Looper.getMainLooper());

    @Override public void onCreate(Bundle b) { super.onCreate(b); buildUi(); requestBt(); }

    private void buildUi() {
        LinearLayout root=new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setGravity(Gravity.CENTER); root.setPadding(40,80,40,80);
        TextView title=new TextView(this); title.setText("C21 Rocket"); title.setTextSize(34); title.setGravity(Gravity.CENTER); root.addView(title,new LinearLayout.LayoutParams(-1,-2));
        status=new TextView(this); status.setText("Gotowy"); status.setTextSize(18); status.setGravity(Gravity.CENTER); LinearLayout.LayoutParams sp=new LinearLayout.LayoutParams(-1,-2); sp.setMargins(0,40,0,40); root.addView(status,sp);
        Button on=new Button(this); on.setText("🚀  ROCKET ON"); on.setTextSize(24); on.setOnClickListener(v->start(ROCKET_ON)); root.addView(on,new LinearLayout.LayoutParams(-1,180));
        Button off=new Button(this); off.setText("NORMAL"); off.setTextSize(20); off.setOnClickListener(v->start(ROCKET_OFF)); LinearLayout.LayoutParams op=new LinearLayout.LayoutParams(-1,140); op.setMargins(0,30,0,0); root.addView(off,op);
        setContentView(root);
    }
    private void requestBt(){ if(Build.VERSION.SDK_INT>=31 && (checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN)!=PackageManager.PERMISSION_GRANTED || checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)!=PackageManager.PERMISSION_GRANTED)) requestPermissions(new String[]{Manifest.permission.BLUETOOTH_SCAN,Manifest.permission.BLUETOOTH_CONNECT},7); }
    private boolean allowed(){ return Build.VERSION.SDK_INT<31 || (checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN)==PackageManager.PERMISSION_GRANTED && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)==PackageManager.PERMISSION_GRANTED); }
    private void start(byte[] cmd){ if(!allowed()){requestBt(); status.setText("Zezwól na Bluetooth i naciśnij ponownie"); return;} BluetoothManager m=getSystemService(BluetoothManager.class); BluetoothAdapter a=m.getAdapter(); if(a==null||!a.isEnabled()){status.setText("Włącz Bluetooth");return;} pending=cmd; scanner=a.getBluetoothLeScanner(); status.setText("Szukam FIIDO_C21…"); scanner.startScan(scanCb); h.postDelayed(()->{try{scanner.stopScan(scanCb);}catch(Exception ignored){} if(gatt==null)status.setText("Nie znaleziono C21");},12000); }
    private final ScanCallback scanCb=new ScanCallback(){ @Override public void onScanResult(int t, ScanResult r){ BluetoothDevice d=r.getDevice(); String n=null; try{n=d.getName();}catch(SecurityException ignored){} if("FIIDO_C21".equalsIgnoreCase(n)){ try{scanner.stopScan(this);}catch(Exception ignored){} status.setText("Łączę z C21…"); gatt=d.connectGatt(MainActivity.this,false,gattCb,BluetoothDevice.TRANSPORT_LE); } } };
    private final BluetoothGattCallback gattCb=new BluetoothGattCallback(){
        @Override public void onConnectionStateChange(BluetoothGatt x,int s,int ns){ if(ns==BluetoothProfile.STATE_CONNECTED){runOnUiThread(()->status.setText("Połączono — wysyłam…")); x.discoverServices();} else if(ns==BluetoothProfile.STATE_DISCONNECTED){gatt=null;} }
        @Override public void onServicesDiscovered(BluetoothGatt x,int s){ BluetoothGattService sv=x.getService(SERVICE); BluetoothGattCharacteristic ch=sv==null?null:sv.getCharacteristic(WRITE); if(ch==null){done(x,"Nie znaleziono FFE2");return;} ch.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE); boolean ok; if(Build.VERSION.SDK_INT>=33) ok=x.writeCharacteristic(ch,pending,BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE)==BluetoothStatusCodes.SUCCESS; else {ch.setValue(pending); ok=x.writeCharacteristic(ch);} h.postDelayed(()->done(x,ok?(Arrays.equals(pending,ROCKET_ON)?"🚀 Rocket ON":"Normal"):"Błąd zapisu BLE"),500); }
    };
    private void done(BluetoothGatt x,String msg){ runOnUiThread(()->status.setText(msg)); try{x.disconnect();x.close();}catch(Exception ignored){} gatt=null; }
    private static byte[] hex(String s){byte[] o=new byte[s.length()/2];for(int i=0;i<o.length;i++)o[i]=(byte)Integer.parseInt(s.substring(i*2,i*2+2),16);return o;}
}
