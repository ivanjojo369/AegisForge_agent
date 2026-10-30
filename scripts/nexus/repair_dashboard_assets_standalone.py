"""Self-contained repair for Colossus Mission Control dashboard asset path.

Only changes files after --apply. Never runs the server or sends credentials.
Restores the user-provided v3 dashboard assets without requiring repair_payload/.

This does NOT certify external source authenticity or repair unrelated HTTP 500s.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid
import zlib

ASSETS = ("mission_dashboard.html", "mission_dashboard.css", "mission_dashboard.js")
API = Path("src/aegisforge/nexus/mission_control/mission_http_api.py")
DEST = Path("src/aegisforge/web/nexus")
OLD = Path("src/aegisforge/nexus/mission_control/web")
OLD_ASSIGN = 'assets_root = Path(__file__).parent / "web"'
NEW_ASSIGN = 'assets_root = Path(__file__).resolve().parents[2] / "web" / "nexus"'
RE_ASSIGN = re.compile(r"^[ \t]*assets_root[ \t]*=[^\r\n]+", re.MULTILINE)
MAX_BYTES = 200_000
NEEDLES = {
    "mission_dashboard.html": ("mission_dashboard.js", "mission_dashboard.css", "archive-generation"),
    "mission_dashboard.css": (".monitor-grid", ".historical-grid"),
    "mission_dashboard.js": ("Bearer ${bearer}", "loadArchiveGeneration", "textContent"),
}
# Zlib-compressed basE85 data from the three files supplied by the user.
# SHA-256 checks detect accidental packaging/copy damage; not source authenticity.
EMBEDDED = {
    'mission_dashboard.html': 'c-qxi!II;)5xwtMAUs>6>}--sITEX64R=;m8fi+@Y&p5OAQF<`g$M;8wVs@OjL%6`zQJc7QsrajPy8i*4Nw$GQF7uW2Op-y0UAK}>+aVL41RStA20v-7_n`>Q{k%t{V>I|%`gx)paH?t@D*c&oya*$w%k~e4}&6K_kJIoCNjPg!{8vr$GtXrz!IJ1B7@*ZndaMJDh@IcJ@?NiGLyOFs%I0f#PIdmE{Rs!V0J5ZqPA1Y%?B@=%Up?Y3^8j9%l`FG_8_g5I%8u98?6R?FG9jfW*?XlY8cr3SqZxp0{U%@SPz3fx3I?ccYgU7DYx5|<|e&Kth2uF%^$3E`i!3)ml;bGR$;fTP|E8=Lc$y;cHHJP;^r`$%EM_2CNOR!?`>t8ifkC<rVv5+!$1By=tJ;)$;$8yTh)ievsfAZF$hP|{Um-nU))D*7JdE{vpyTor}H>|iv4bb?d$Li8jKD0;aJeWEEP<ztS|?j$d~Uj`X69GURd_b;58_jNNxxHeOTAf?)eFcoaeA%Iv<aw<cxSSW6|4ayqrS~5_BN-JxcD|iX<nU$_<p>$gIRg@fG=MlqAAx#udYQp*pa1<2?`bV0#;uEA~bnc&c&SZP?KA&xM5J!?6+GB*iQOh>48t#7^VrQrEhbP8qh=21bg+(E6qRAP6SK^j;X8WCndFcCKOKj-pUUEDdC~FLEZ+YAcpM?*-Xm?MH1=*k9y2(Yw78ImY#R9Wc8`kRcJ055r)MeUJ}(#S^jB3Rn(<2{|z>62YKQ?o?A6X+?f)IUXvkiaf^v>42=t-~`~I>6gO_KiiN>r9BE(&K4L!Dn19{ouJv)>Q)rfE<0f@-#~R}A$+(L_ULYTAPlsU6ha;5;Doi@6ofQI1GodeGc-?8D}nz_btXLM1FI{@Krluowi#v%VT)bqPb+OMcDxp92%sHTDvaXg=x)yLA~qed(c=W?xbcC7y4j`<C=Lw5SUSDMSCmo^c2dn~+{z5#$E69S(+~g@FNuravtX<#^e~Ah^H~(L_xSj18pYij6?DcAU;q8O3Y(YjDQYZ3JR|q<(TwhEiY#8fxt}~lGs0&67%fKQ$;;nnEp5V>5={Xexbdnkz>V*XUV)4p17(tlHP&g{R^zIx%aYM_^}~tQQq&ryTzzu}aRbnmXfR8Ya@%Bc+|1KVEn`qt3~EeAx-cXYUn!A-sq%s=`Iob_sq@X=B|_d?OO*=~v{>xX&*~%h00F|+snA|oojcHbhth$o0!c?)mJ+glxIK;VMV5v$`ne>-#YWnVkw9fcpZEB@Bukn81x(veslYdTOW&8Ild`x6Rhd6Y_Pw!TMKF#a_Zl(Oli6Cgz-rP?I5%=rX1pPt5sja+OjE+zLgk40l2A_k<%!f*`<@0mIBc+QR~((ae49{>L@hJCItW?M?Wx+rO?#b5c*@?Ks_md(2ItQX)jJ>T$I9*)sa+d$z6rtwbrZ|Q{5<2EWsSRBv(0)!DJ4}c*vex&%gPq0>qbhvb0esfo$`AlsI#oeKl+hQX_=vFQpKvICvGJ1wx1U&n1>U}^d;AH`ZcDYBVOXHz+Z`_cd0B7z@%Z+l2SLDa_|<p(j1*v_hpm7YVelB&Vw@Bc;|NafAJG2mr4LQml!xk^T(!Wyq*@LV*_{_&V7GJ__}^i@n#^j<Mod8W(S!ig+ex1D!a!_VYWgc)qijSye>es#MQbU!VqNAbw6v=vy6$a0E0z)57~NnyC@{&$(_A2v4TciQdKCA_2sl#2-;;H3c%E!W2Hs`sN-;r`B}Nem9)C(kzc<?558QC{xte*GM(Iw?oMt1W$X35;rp$Jy=H&i+R1Dg-7oyonxz#PZD@y{PluMtbV=#ppYFi@4>{()0_7H<MDIt_C$tCDwwyq5)rC#@N*i*_^HpdbUcOl_CgV}ujSSrU3&Z7&yRgl`xl>Bt(Rdui^ENn3Q`r0}E-vkTkJ^n)PFwuKu=x$Jd53y5Ura{R6DG$?%~I|JZua6TFwy&HcDjVNK`D3mKjQMg;P}yE{BH7o&K{no%gKE-b3OKahMRW(0^g6NFyLB7RrcjO+<9C_|C6+jtI*5$;AgDlAZd^!Ho0LlO$9BNdGSn_%NpTx*T(uT-H4>13qw}FMEKBcfOCPgeYp6x0OdmUd|%U`?+&v%UIl7<_%4ycuhzsgdTefzEoyuZ{UCJD>y@Vj<=)Nsr;+Ml-qm$??RB~HZr8N4*0#}|oCWqVC?*JpWpsLEp^!Vje;-xDRd?rZBG=mEixGa>cl)LT?JAU`gH}cLm{dJ3`5sp^x&F-sO>-l%w9>OV+L4q1(=}W5*tluGSak=9zr1vGx%EIQR>wXY8lUPo^NRV`rZ?f46|PhV?b!df4>}?BQwOO^^5>uCa<v9v_erjjbR2TwUcS~EOae>bnnEaIkNDlFcl+Dlp=C~8V@S6ldU&BHIVR9!7VeoqeL@Z8@j*nUJoOw_(a#N|U3V|v(yI=>tfqLgiIGU91IPSRM*Gpb5mp|b6(}tIWiJsJF4>AC#FyYOCvd|e>n>|VN)UE4IcHlC5Z_50&mHOQbn3d>>JEpPD=mIC=+j#<e!WJ_n&jGZ^Ov|wrecki0MM<VE!?X!9O0hke*{PWGl&',
    'mission_dashboard.css': 'c-o~|TW{;Q5q{6F5H>hKvar0m-wZ6eC{SP@3KU)Rp^ubE*=%G<C8Z>;;QziuN|Z=RNzOrEd~6<aIGoEj-%K*rG~G}%Rl{svT*#GV7_paA+0>m~qARU#+3$5n*n!`8N3M2H4>oGsirC}A;SX|di1n}E55F;rssl$gw>e?7Jmsi(IkTp(arWpWPU+?+c~SOAiqbFh`_Dt(yxW}qkJjfbZx|-bhLwBX;M;IT>`d#-IbCUOUkE*4y3BQ)*NZNfAq<z=XYntoyC+grwB^J?os}ftEIa;ocywbQ1*zp6ItIG6h`~lH@V`ADCavyAeu0C-8^zrvgX^ALKXKak16=p*`J@!FP~SDD7GW&Z^}Y7p@)Gz6;NyP0BHmpH!ULwft<WtiEAoCiqc-zE)CpDeT-y{3#<PM5gD!F%DMyH4FZ1y;qxb~h3Syobm5c(AIT=NMpR6bB(DTfVU`e_Vj7u^<hM$w^QBXQKF?{2ueiB5Jh|Gm2@us5Kdh}70#KB3t#O4=9>zB;AA9FAx9>iki-jC!)a@M>Z_!WT(mpfhY>WXxrvkgWsgw2j_NUoz^Z=>#U=qSY!jFQ5;UtCRy?{9_JM<)O&6|Kn}79d!eGX(V=3(nmyo~};3D@iIeoebnj^+2m#B2bwQ=eQu6UEM&qjd!Rc!?Y*ONeFwkw)D~{2!oZMe8=#UJO7-_lQ_=ChK>&^t0)vFETP8f?E+yUcaf}a-WY1{#}?J3IvlG8o^d!{Nylixf5r55Fx>CD*emu_j{Gzz_CiZtFGBl1${*%6PprwS?frysz5RsvPtQAA-s}P_04te%jGdG2jgWfs65vDEHP^}X@@A_-IhjIlvRedUrI}Skk<ucsn&M?GOg^Jv1jDs}Cwxce^%jfPZQmJ&bW%6;!o55S0uuQvL#9hklCq4;d~vF$oGPGiOnB!sCY*d$M(Yb8Y_|@@yocx1!=!+-bD^$o;$uILc0y^=%7MqYWNpWFcose5u)A%j^r6I9+R>)YN?O5+b#y&$5yZ)ezh%R&%i5$eL~QqzfzeT35&WE8npeVxQ9zb6aam0`e}Y7RR!30L(N{9eLNocTf?WLf6WCtuJ8}Z?1U2nu_zGehLpn}R#f0Q#pd^<~(R2HiathiUq@T^Tq3Jt;Kbg1qI4#POIH&1<?4~R^>BI&lRlRO#L?(B)?C6KedYF``ue$r=6<&$XL<cT!@ks6!5e`)Fb`6B3D+|Y%*=Fq!A2y^{D@+18RrS}JXN<H2bx;1mE!p!J*)q^ZsB=X<bM6LI7^xsj0j7);(#4}xDJGf1b*|LDrkTY%*3@V1S>8Ufd*pgQULceX$N%<}bRQTZ^$_Gd<l~4G-}7+L?TX%?ys@yu<E?4MIk?9toUIYcJRjx$I>OkXy~|G}5L_weU;zHT)=9NPLr2~_TjF0<k+Pdgx*!{7S&qEKM>9s?G60O&0T(yO!*Y$%u7=VhR!+Acr@S$4!a)!y;IdUcmhdp2v%pLBV4|q1i>Pq00l4l8C;|WqEovj750XaOv!EzmnfO=&YX|$M^VS4<>anRnG1n}svLbcpJnUF64*d-@OU9-I#zNp1bvoXw(}JZe7D{ovSEE~Iy23$FL;PL-5V#xuV7fUnQ_zbYX}Wi{5Xc1qt!34`Ws=V7DuFK2_Vbp}6R5tuLAfC_3YFL+!OFfXJ{E>|0ldOPk_3Q$icSg613XI-GQ;;eBOo>Yf(|GmvAM@Gc%P@C+ei(=y)z5hgMe>p+Ce)caO4Ld)jKSJ0%)Sc$t2Ohk;u{h#~wQ#&3BPJ2v>DCJ!&z6Rz)(3Fo@#MqCvJM0?+3d4D%eAsRlk1?OGLh(~+^la2p_;wm(#2cI=UElQ~@KglPWEY{=c%i$2bVIrRN)3*?ymNxx&K9EBT-xN0!W^8yQ3c&AEJfJou&(@lYWv3M@f9kM=e-WSbn5y_*%oeQ+5nv{z2ZceS0DCy&c81-#SSOIKn+mC4w<>g3$xQ1%#-Z6JZ77X2Xd$H>VlkY-r{eW3^9Iy1#y^`idWgMP1kiO-!0^OGKso%8qu@tdez;IEfgqWvgJNuy*gH1h9r@@Uc-%Sf56QEr`Tm!T&b^-!hkOrN4hH;A1#ayC~)tU<wLD!1b)p;M{B_K5-5(j+%)KgomO8`Fn@wdOjcl)2@+*gPl{t7j?sSrQRJJy9#C5D62y!i0qmNjPv?01-KvF=u6Gn%hMAl2=4p%}U|v#6M0BcoB>WC6S590<97O;i*`?&zCJM61;%9Hn8B>zil|RjR0ZzNS`S{j^B$M~pgTv*yi=jPEFM&Jy<uF*O*>eH;)YQJf9&QCCaUg074Ka1<9>VKuWrW#M91-#Dg@VxD2?l~B)Hs$~SNC0@x_Y!f)}A}@|iHcR|4m>aGlCpp;!<&JY#K|N}5d_$~O>Xlv*BWFckV>e8K)KI`jUg06&f%Hmbgi8eF-K`R2tX`}r266y(;X)jy$<Rs9KmD~RPGY=BwkkSk!@Y50(o9`3BTy_MO4N0eG)ajy0a;D}yTw>(jEtXtT1226F77)4=Wx`PY*m1g(0ByHbeRsOJC$+OXiS5E$&DJYjFTZvuPHE0SlkfpdH2U1g9G>HYxisT8XAJcmth}Ok`l!^sL04GC2tUq==^n2ip<NT+GPVZnSIc2irHkIuBnkO!_&rMqfpK8^0$_?fe*oX|I3wNik_AdmOAjT=fNbP`y2}x)qN`<4n_gvI9ngmSrg4tLu%6~YKA$zM}}46X^rqFTFk?TPqyfjwe`*0k(%6*BTo5L)c5GKFqJtg_5`z1Hi1^AB#hf2ye|lb@--!&`1w3cJ)<_P5(sd=KRo>Mv-J-+2+#mDn>Rfxh?S$dCd?8;VJOL$hQW2%0`Lc6oi$FWL=*AzFAtB?syz+AR^hL8`bJM;F!zP-=uEh96EB$w3xH=y_9u-|xUQ1PL{`+;@V-`aXEp^5+{0sWA;pWF?Af#~8wG0dsI$p|j&Z^^WBDK)P5K*yg7XSSI}@-Zl8t+2y;9x-%eq;dv<Oy-RkG%46+M%GqM6)oiL!JQ#ywuZEnxiuRrWZTF+o3cBR9f#V;?6K&@Kp`|F_i?;JS+4-HYVRZ0S2Z(e8pN(P#mCF~IOyi?9s_O{D8=nD1$)Yf|MKigy|B73nYv;_&NO*{Z%5FWN@lU$0``YRt2p?S#Ov-%cx|4<^T$6Aa+tsP5??Oz;oQC$*v+OHopU!OK2rY+XlWtI^d$7JTs6JsC8sIh58W0w*tVK5LL8wklgY+;>fM=dNK>;v?;GpOD;f!zToH-0&5-HT?L4Q<L2(fh@>ocJ;ZQ@kSUsAz<E`<9KWQ9CN}L80gc1-P^2-Kdh!BdI)I*fkdTSq7S5)6O<n^#}^XCyhJ@o%zI>!|Kh+-V=U8Z!zNrO8tKH^yPs70C7<>;#mP2<WcPf3_!C5G&GY',
    'mission_dashboard.js': 'c-qBV&2rpGa=z;+pa^-;Sb~r=v+MAZh8S86S0jghCPaBJ3I<sOs=FbffnF54B`(<!KKTK>>zgmWIARZbnu8;JG~Z;O#9wCppa67}(%2C`NbIi4%F4>h|H=+mN(QQJimVL}?>&4N{6>mKHbK_NTvlx%O3aE&6%<vl6f+qt<f3jagSsj&li;;n19mAJRj4)&%6b+DbrZ;JUI#^++$&`pTxf)85Ml!wz@(CuH=txGv$m+K<7J)AG4V)`izb^FYx%bRq%ipY^zdF*SE>yj;9HRg>60L@v(*A*CNtR{my$mI=JG{8De}kyH}$HOwEilWttiTaFpPsa$i*oR!iVM)#+Hp-7xELv%qNT$m0E%{jG0g2#^PPnRe_a^dDe*K{2=5%`(|k=F6u_;UGUisx6MicAOCS<#X^=vB{6J1=^5-bT~*8(x`cM6CJQx-t{zOng;WZ+5k^TXKex~7s>RVk3g!<t-6}A4F=+*EB`bweuf;-QJFJMV^eJSSIv(Q6MJo*(X;pMsOebz(B+JBoN!=%cA&mFabXDbY3hHwL_2^1ZAYavt!J@-PwyTCn^9dDsK3PIesx~HaJ8FGmAq{1ox-$)c4)yv(ESIv%pAqbYSGX<to4;#TLN;I5xtvt^FS?@tlSVE}kx51Xgp<f$v8u!xLL@Fqy(Z=h8Q?jj)9`GV7wa$%<zm@F#0b_$Rzmh5-1gw5oVIaO%;xUEP5mkLIAgHoq80m?3iU?#6o+alDollp#2;^)x|;Ef#%y_kS)Y`JBokmaT8nZeW1Lc!j`y-Gx6q>3?O)WN4K)PU+m><l<{YVv-(3zScX0Y?Sx$_=M_1FjnP`$=0_th-=HdhNGU08YA4V&q$iJxvWDsJD!rw)k9?2Ht>Rw=MQ(EMQMnA|RrF(mQ6Lkp4Mk?7p)fNh?k3tB$T9vJ+@;b;0*#Lm;%*{{WP_6#@PyZ1fgeQb210F#K3>JkdZhoo~Fb^o5CjNdUn@a}8S?Kg(0!W2rP<SxlSY-1FRQHKh$(PtCY3tbxhYbY?UCS`WB#8>hOo}{Baj1y5={We&W8g=5sV3T3M$v=G$0*4QMcm8N`(Ovw!R(M(+V4p5b-c*0ufvnVur#A%g=Q5v0mh8rkA~dH3<rZcke`<&%qOg<3f4HBAO|#+O;H0`vXhZ;QCOsHHBUByS}Brl0anylNhHBy-KYB=h}f+-Eo~a%bcG`jAy#}LQvuwc$c;qMg+MA|mY*oeL`yf8J|P)n0<>VI>A?4W^NkfjzOTLzhsGi9tJPv5;IyJxCPu2k{MR*jA1qfSe~wyN8fOPjZp3UelmdA<l!H!<G{MFO;OYeIdy_tJ5bpth8XfwCI<UB>6X6~}_d?PGCLjn{%qQm$#j<#~e*DnLm3sK#N>&-n=RdxE@vL4f>x%plxUs<uaBR*aULFKVJBNWLAqQu_n5nQ|xolqP15AK0CJZnO4sCZ`KkKGC2Hbf)r7*HYccU+6b8qOOCef7+qEbXmW>b9X6STK?*fUAQ`<p*!2pi;3f@c!62@-VQL<tsWNSTs|1V~-krqWIr#Og}#Hqc|%dCbbsTICsbRSnf0_x>lI+-eMnSd9RFplP722?JY^0QlG~GqNf0%|p*vA+nM*wQTBqm9_iRqHJZe4I-P%?BlX7s&*f?r5=*>1`ZQpY7Px*<g6f<b`VtE<>fVK{AycjH^0S_j;!d=PVK%~GMAohGC^bbuKvg;QJH9naC{6TCb5h2Kbw0(T~=A(L{DqyQnYh60%SC`6h`eF(L+$lPr-51fC1st6%h^*VuHagT3gU1vP7F;Mk=VXlBxJ4iZ+-^(lJ6GU%@`l>-->me*7+s=TbmD)xj08&SAIyAc&lUbCZ}nFuJfgkDq3lTq2l8#8+Z-0(<yDfuEbWOJCGMSTBk;j6vNTA$C>olR1Qc1W3_vbT8=`p;kXSrzJJ1KSs`CyFtxWsQoV=JvOl)q;bR)&Ge#L-~4-7faj1L0f}U}iP&H4MEh$SbwP-6=?lX$2TF&WSg$gP(kGLvO~lc<S&;CFfGhk|Hy@{E{pqZ#+cO}Y7PA!&9*!^Zm;}Q5Jji)lz)Zjq1{OtNmzyJO+s?R!GgYseOr8}Loddcf$smljd}*y8?Wb1ABX53U8O~t#f!f+Ah%2;I&Pb;6U0ZkRe3s2cHIw;SLqguPXC#otj#HOGw-7h9xWM@X(vT1Mi;Wdz&Rk&<yp=^&WQ8ENiitG_8>54wnTe*<k;~0rvZ56WYuC3?YZzoa`d3n{SFsu!ZMQi%7^J*YfUTIjo&2`J63TcL(Z7pclP7;Ap)U03)5KRz!CkP1otRp~aT|ZvbxEa$j?s52(RX8tY&ZhORdNO|6D)zhteJff{aUfPuZcSBoY+8R#}2<7@9@g#PYsS{t48s0Vf?YS07D(d;lguAS`R*${P6DGkFa@z?Ke?!Zh{4CUN7qWa=V?d85TisMsRp<WbBP2o#-UU+8%PC={8@=kwItLIRevrYp)RwW4fcVfDulA^(fq%7;H!3H+G9~Z%e>uCZmHe)OpKY@qo*jRr(Y$#AQ+_{>uTPO`ueC)cblsWhKVFEow1^3&NR{vYNpc?g%@k@HBVC9U8>C7!M`o#=XPE;Sw1#6boTkOo8KET(W6qYY0sx*-!!83$#$W@We=07n6{TAu6H0tVg+Mt8~kL7pu0dDJ{1lNsL97NoQ0}!$Lou>`!xg4+t;2KZno)#;{`({P%wbdc;WZxkibF$7`_qB|8ZT70TetKZY^6HJ6<ah|!u)#VK7%L@dtyRP{m<Z2F|X0NSb{qsXB&1BbGt0;nAgiG^PLJ;)V`D%Z)vm8W-~d+88BkPmegA<VnDf|dr?*Q_szi|Ifei$*UYrFC)mTjw1+-z&61l%Bgz0@zwG9<~dhI=l}!;pIs0gIpk+*2|m}HBywL3>HLc+O%8B&rIel&ZyqYg35G}cp{)c5EVXs`|O7ozk3t>@Z#j%%^#t_Zw|-+Q6U48T4k$}0#4z(*_f!*3BPsoWHUw9t^fy$ds&eti}*@@h-<8&$>E;U7p6#OZ~*1&^x(TPtX=d{T-?5dYqJ*m9eue2-V|-!67$_(H4>=->76`LEq<9gPWQ@eJO`GTY8`*&^$ArQ&)9;Iy~t~|6-wqa2lZB#CcGMgh9^Hf-T(Gie~W#8XWVmM>x&qWMFq@#iq{J+_SucyS%@k%Jd3W9^OO(E)j*Y!OME4klYu28a57~RcUpCQ{r<FHpHN5_+LVj9%#R{Z6pV?SJc@rE9qJ7d*sqBXLWYXJ({&m%#fUcN##g)Iv%VJIe0&&N|C|#Cz51uay%!SlaF3DqfES`HJ6LTW0sRobc7)27#@*k32i>(ok+M>d!5}^xCKuJm9T&n=PTlTLoWN*C`@V`nxB952+ub-N+J5Ah2yJ(p-6lTvA#LnY%pJ~fhqNj&QM9?r==wUWIcpC$!C(LRUvQHQMO2aM*})+e1hV+-W5Er#MOBAJ2DaF}Tg>E92<4vECQ66y9C+D_vw5)NIm|hk_mZO8C5*5*<mgy(@TjvP3P&$K$QJn{PF7S@#XGQm!A8$HyMqRP7C1_scLXnMiV!G9Qk0ayCW~sv1+hUECW&V%$!#F|Mp#zpEPiCbdS<jUUn13<nazM-wDS7;YIFGImJN7%wn!vPp_I&zjvm3)7{-k(mPn>>n+EE)(57N55ND%Vxl*_@27O@%M9&npbt}sBm1yV5LVTV)#!^WUFAEVQ^^sL4(1T;Dkw)3^q1;okR6ko)$PQ0Q1>V08^$BU2{%{(<5A}I4W<JoEPd;XPUr$eamJqJK)vq;%5<{8Wn_tUXSe8p9C7+7Cou^PQcjQO0{#aC#$B!Px`1gy6;8HK0{vk6ciZ*|9ZjF+C%3>zGxuD$5dY3-;z;~EpANoZF1}$`!uK{)0%eXAJ6sab%wN_&oZb@l_XuNs>q_5G%ErUo8s3D>rmSfef@<K;V2*P@V(03+-VWcDDc60nasaSEb(h&}2n<gNxN3vliV)oCcUNsR~*<eF$EH5t43?OM%6#4X947f=~Vsr;)@8ABY9h)B3GDXAx>JR$@wF!>StuL4u{!z*DY+8z0-y>Yz&Vi?;Z0Pxh66-==wR1;|Y^XeXqkWzOWXB;IA-0UjVYk7bT>h~v$};#Q8hoY+oiKc@8Gm*2=aviOev#ZWx{AYEZMhq0)*+V?v~)oVt-;1eZPnK+St8d7y1>^}Uv$}{A>00}n9|sVP`m};YhRzVaO*ADZgUox$x1XNxi|k+(Q9K8z$0hiJd;{cb3<lI6BPIdRRO|b_ext)8yomR<omykP+7*<>YmgH$_i-FIP{!!po--?p|^Yp5-sF8Q&Gx9X)ftU0?;)uoNKlcO)eU+u^Yd86S2f?thN8xzKe4`{Y|K5O4*H`t%u<g7s6FLj-Wionce^!L8*>8`li?B+PnnN22`Agc-g+*%~KJ*yG{LzmMY2p-MwA{6Cui}_8bJQp>(Xu-3h&Z0*yZ5Y8lCm4`+m;u4>MV9I*L&rse_}h6~Rh|6OEX2D`mIRSx$i7K5w5g+?^Oqfl!)6^i0M*zI~hy5-}OcTc~6bK>Q)C&7!?$8X;qpBzACsu{G)mI`BY>zS$dtx1>UA8Z!OK^%x!Q6?@G`8HR#<@f8l?4FYBdkocRU$6<*`LPp*V1cC+=D~0{aTCGoH-sXSSZ=5?`F>^`j;#=JZGfu;&-vA8MN1fG)04P01$@@9*;l2~2;A60j1t|>NJuK`sMPpyS!pGp((--%b?iR;eBxc5i}lT4xExAq!E3Z*_If6~%7x8uRNIMoO<7f!oey#l!pIR=k{Ak4$*agl$w@!2bc#dh$-(u-v{MO2(<uZJ2~D@H`i^9qlN!BB3oMqbs*hFu2`P-7$!tmV$YhX%VaWX;SEVz1N=#op`b2xSYBTJlOa1;}M4(jWlXSPKKQ&4}y&BM)2AM*THKgu0Kh5BP5JNBYL!<u)fE0rt^z;klD_x%p-s(i;5-AHLkEK!tARIo9ou{H!DbY(<IFcgNHB?>8rZ1e{Gq~#;BGS8l!c>=ijYE@V>UCG>`k8*}rbqU{<1LMhi_eBLn1tLdc`<Q37(V8Duj;z&JnHG_6`&3I1iPabq$ORX5-x4Dka;nF*Y_S2#<@6^CRhzjX)X3kX0a)To$l&H3HN9{?cVR5u`LE%*E4yrO(X|T4?DYNu6DmKd`-`YAyv6}ma*R-HFR8Gn}&}2rb~ovi&werDz?#8zt!RgC*ccN%%bbB5M{r1TRV$vU$+Kk%oouuj~Jb3+II|Z#5`pXxgHv4XZs}gUVB^5+UjRt+rZ{(7{f~OostW~Hl||Z3&upMs1uHfijv@au339sbFp7KDQmducvPs+B_GXbTU$xazg)eyD)Z<O#XF1*sG#TRHl;s*?n%FX^yoJAUP4H9RPv!lmQ9u-A}bCw#zJ&&Pu^-B(DaOwdsfm;+1&6u`lf{jB74SGg2Pa@RIEy#;yrg8kovt|EiPn3$I_%kMj^L^0KMHH)5uf(AfU&1W2wBi5o-T5606rbVfD=oard<i19W}BCPFWrsp<qt*`^q-2n-c$bgv%KJL^A&A#@d9DI?-akhhM)3>FBYSVzEoW)on$5P;x%aRUiVGM1FSQ%AO_(r&qrpQh{vqiT~H)tU67ZPg|{v%BV`=eJj$MztrR%GI6J&!{+cY}Z|eTWU`_Ch4(t_y9SqfzaTn0z!jZ>mN4cp)2(w9#?N76{p53B$h42?|@4H@VR8VY;VRTLL)?Cjj1eoMJ^qQ14BW*tq5r<aJbi>cc;(nt8OPfBPVL#II&=tIqcr`p^_dvJfg8io+a`;<o|gz@lsw&>KU~UiJeDq?W3-#3?5gR;NZumhD(!)D9aPswz%TS?Sw$|mJ=2re$0FF^P>xZc$!O%s`Q+69z|ajb<o#a@8-*I;yw14pJRTHzWYX8k+Glo&^t&vz98Fbks9{vy-vGk881t3C>?F7L8xcc33J@Q_d-=FuS=HOVRT)M`g{^ym}rX%a=Gy-qmZ!a1orD6*qH)yEb?=qyC{aT=Gr8?$LJv~&Xo~1&%|rwoxvD{1cPv3{DyAI4(DA1(k&0*yqSBR4(@*bzGcCd$L~HgwI}Zq(Jjp!+%BfOIcyiH>AdKK*8ZHsG-+tdHBc1KvZcl^+^Q)0iQtRP<o{!be%Z^GydHh}cdu)`-p*g$EW_@Bd2D!DEmy3#{a$WL_j$_1S%3fCx6zet5<V^w_~<_23mGte(nF{O+}3yBl0ph7a?@`bF^%brb$jzTGW)B$+Qc*sy8V1-r@2<@WpPH(Qri}hUxxhC8?2xL>;dt(OLxOV+Yx8l+YaqxzU@WXPER*)Xu7pkyqoQ8&e8d^uB~*f>+Ch@`mARNyrl!1VBQ4k07sLGiu*dQZKSr0=LexFoN!XfKg%oxtyI*I@AtF<9IW9V^L5+x8&Hput~WTk>%e;{7ou!<(|a5-9X~=1_w$OaUe0><_1n&OC)=Uxc<+8sV6#O^3WFcHM~b?7sr*WMjjWtINsaz@{oDrAZuKRJWB;@vMFsH?yDv~x#;+HQS_OC8&rZQforXf)QGYwtv{BP1x@%?p0^_o6Z_9C7CJ^eHUu)?yIMw=e_4rm2wbQ3ZSG&sDLZ)+&)2P*ST72I2L~4I?am5u1z<$xO#!hMAk#4hLUlA-mxYCqUV&|F{d)200zmNHclQ*vu#erWj)wQ6wT9sw|t-FA(gINDaiWA_HA2g{~xZ}UuSvxRuu7#SVJ7rxXVpnDTR^oJxf7QB+aQ;0q-{I!oEk9$qv?bE5zrCZzZ?&?|prrh|)%8n%@5CwD_!pUs<9`4^iehd',
}
EXPECTED_SHA256 = {
    'mission_dashboard.html': 'abfa211392c76992e028bba3507de8bf156fa7d3c8d643e20f202e27e8095cd2',
    'mission_dashboard.css': '7b2aae94057467154ae911b23823e4678ebd1300f5ab686195c5f896fe2582ba',
    'mission_dashboard.js': '67ec9d7a417db17e8e367e8044473a81e5e796630ffca306e471b61319482a2e',
}

class DashboardRecoveryError(RuntimeError):
    pass

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def extract(name: str) -> bytes:
    if name not in ASSETS:
        raise DashboardRecoveryError("unknown asset")
    try:
        packed = base64.b85decode(EMBEDDED[name])
        dec = zlib.decompressobj()
        data = dec.decompress(packed, MAX_BYTES + 1)
        if len(data) > MAX_BYTES or dec.unconsumed_tail or not dec.eof or dec.unused_data:
            raise DashboardRecoveryError("invalid or oversized embedded dashboard asset")
        data += dec.flush()
    except (ValueError, zlib.error, KeyError) as exc:
        raise DashboardRecoveryError("damaged embedded asset") from exc
    if len(data) > MAX_BYTES or digest(data) != EXPECTED_SHA256[name]:
        raise DashboardRecoveryError("embedded resource SHA-256 mismatch")
    return data

def safe_path(root: Path, target: Path) -> None:
    try:
        parts=target.relative_to(root).parts
    except ValueError as exc:
        raise DashboardRecoveryError("path is outside repository") from exc
    point=root
    if point.is_symlink():
        raise DashboardRecoveryError("symlink repository root refused")
    for part in parts:
        point=point/part
        if point.is_symlink():
            raise DashboardRecoveryError("symlink path refused: " + str(point))

def classify(path: Path) -> str:
    if not path.is_file():
        return "missing"
    if path.stat().st_size==0:
        return "empty"
    if path.stat().st_size>MAX_BYTES:
        return "oversized"
    try:
        raw=path.read_text("utf-8")
    except UnicodeDecodeError:
        return "not_utf8"
    return "recognized" if all(s in raw for s in NEEDLES[path.name]) else "custom_or_incompatible"

def import_origin() -> str | None:
    try:
        spec=importlib.util.find_spec("aegisforge.nexus.mission_control.mission_http_api")
        return str(Path(spec.origin).resolve()) if spec and spec.origin else None
    except (ImportError, ModuleNotFoundError, ValueError, AttributeError, OSError):
        return None

def inspect(root: Path, *, check_import: bool=True) -> dict:
    root=root.absolute()
    api=root/API
    target_dir=root/DEST
    old_dir=root/OLD
    for p in [api,target_dir,old_dir,*[target_dir/n for n in ASSETS],*[old_dir/n for n in ASSETS]]:
        safe_path(root,p)
    if not api.is_file():
        raise DashboardRecoveryError("mission_http_api.py not found: "+str(api))
    raw=api.read_text("utf-8")
    matches=RE_ASSIGN.findall(raw)
    if len(matches)!=1:
        raise DashboardRecoveryError("assets_root definition not recognized (expected 1, found %s); API unchanged"%len(matches))
    assignment=matches[0].strip()
    if assignment not in (OLD_ASSIGN, NEW_ASSIGN):
        raise DashboardRecoveryError("assets_root is customized; no automatic patch: "+assignment[:160])
    if not all(name in raw for name in ASSETS):
        raise DashboardRecoveryError("API static asset names are not the expected v3 paths")
    status={}
    to_restore=[]
    for name in ASSETS:
        dest=target_dir/name
        old=old_dir/name
        target_status=classify(dest)
        old_status=classify(old)
        embedded=extract(name)
        status[name]={"destination":target_status,"old":old_status,
                      "destination_sha256":digest(dest.read_bytes()) if dest.is_file() else None,
                      "supplied_sha256":EXPECTED_SHA256[name]}
        if target_status!="recognized":
            # Existing recognized old resources take precedence; embedded supplied files fallback.
            source="old" if old_status=="recognized" else "embedded"
            to_restore.append({"name":name,"source":source,"destination_status":target_status})
    origin=import_origin() if check_import else None
    mismatch=bool(origin and Path(origin).resolve()!=api.resolve())
    return {"python_import_origin":origin,"python_import_is_different_file":mismatch,
            "asset_root_in_api":"old" if assignment==OLD_ASSIGN else "new",
            "new_asset_directory":str(target_dir),"assets":status,
            "needs_api_patch":assignment==OLD_ASSIGN,"assets_to_restore":to_restore,
            "api_sha256":digest(api.read_bytes())}

def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".colossus-recovery-", suffix=".tmp",dir=path.parent)
    try:
        with os.fdopen(fd,"wb") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def run(root: Path, *, apply: bool=False, replace_invalid: bool=False,check_import: bool=True) -> dict:
    root=root.absolute()
    report=inspect(root,check_import=check_import)
    report.update({"mode":"apply" if apply else "inspect","completed":False,"backup_dir":None})
    if not apply:
        return report
    if report["python_import_is_different_file"]:
        raise DashboardRecoveryError("Python imports a different API module; check PYTHONPATH")
    unsafe=[p["name"] for p in report["assets_to_restore"] if
            p["destination_status"] not in ("missing", "empty")]
    if unsafe and not replace_invalid:
        raise DashboardRecoveryError("existing custom/incompatible files preserved: "+", ".join(unsafe)+
                                    "; inspect before using --replace-invalid")
    if not report["needs_api_patch"] and not report["assets_to_restore"]:
        report.update({"completed":True,"note":"no changes required; inspect HTTP traceback if still 500"})
        return report
    api=root/API
    api_content=api.read_bytes()
    if digest(api_content)!=report["api_sha256"]:
        raise DashboardRecoveryError("API changed during inspection")
    for item in report["assets_to_restore"]:
        target=root/DEST/item["name"]
        if classify(target)!=item["destination_status"]:
            raise DashboardRecoveryError("asset changed during inspection: "+item["name"])
    backup=root/".nexus_migration_backups"/("dashboard-recovery-v3-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:6])
    backup.mkdir(parents=True,exist_ok=False)
    shutil.copy2(api,backup/"mission_http_api.py")
    if digest((backup/"mission_http_api.py").read_bytes())!=digest(api_content):
        raise DashboardRecoveryError("backup verification failed")
    for item in report["assets_to_restore"]:
        target=root/DEST/item["name"]
        if target.exists():
            shutil.copy2(target,backup/item["name"])
            if digest((backup/item["name"]).read_bytes())!=digest(target.read_bytes()):
                raise DashboardRecoveryError("asset backup verification failed")
    report["backup_dir"]=str(backup)
    for item in report["assets_to_restore"]:
        name=item["name"]
        old=root/OLD/name
        data=old.read_bytes() if item["source"]=="old" else extract(name)
        if item["source"]=="old" and classify(old)!="recognized":
            raise DashboardRecoveryError("old source changed before restore")
        dst=root/DEST/name
        atomic_write(dst,data)
        if classify(dst)!="recognized" or digest(dst.read_bytes())!=digest(data):
            raise DashboardRecoveryError("asset copy failed: "+name)
    if report["needs_api_patch"]:
        current=api.read_bytes()
        if current!=api_content:
            raise DashboardRecoveryError("API changed during restore; see backup")
        code=api_content.decode("utf-8")
        if code.count(OLD_ASSIGN)!=1:
            raise DashboardRecoveryError("API assignment changed; see backup")
        updated=code.replace(OLD_ASSIGN,NEW_ASSIGN,1)
        compile(updated,str(api),"exec")
        atomic_write(api,updated.encode("utf-8"))
    final=inspect(root,check_import=check_import)
    if final["needs_api_patch"] or final["assets_to_restore"]:
        raise DashboardRecoveryError("recovery incomplete, backup saved")
    report.update({"completed":True,"note":"restart server and rerun tests"})
    return report

def main() -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[2])
    ap.add_argument("--apply",action="store_true")
    ap.add_argument("--replace-invalid",action="store_true",help="Explicitly replace custom assets after inspection")
    args=ap.parse_args()
    try:
        print(json.dumps(run(args.root,apply=args.apply,replace_invalid=args.replace_invalid),indent=2,ensure_ascii=False))
        return 0
    except (DashboardRecoveryError,OSError,UnicodeError,SyntaxError) as exc:
        print("DASHBOARD RECOVERY V3:",str(exc),file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
