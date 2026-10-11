                          window.Ball=(/*#*/
                    function(){var W=36,H=18,N=40,
                R=.19,P=[],G=Math.PI*(3-    Math.sqrt(
             5)),k,y,r,t;for(k=0;k<N;k++        ){y=1-k/(
           N-1)*2;r=Math.sqrt(1-y*y);t=G*        k;P.push([
         Math.      cos(t)*r,y,Math.sin(t)*r])}var C=Math.
       cos(R       ),Q=':-=+*#%@',L=[-.45,.55,.7],n=Math.hypot(
     L[0],L[      1],L[2]);L=[       L[0]/n,L[1]/n,L[2]/n]  ;/**/
    var T=.45,ct=Math.cos(T),         st=Math.sin(T),rm=      /**/
   false;try{rm=matchMedia(           '(prefers-reduced-'+     /**/
  'motion: reduce)').matches}       catch(e){}function F(a){    /**/
 var ca=Math.cos(a),sa=Math.sin(a),o='',j,i,px,py,   r2,ch,ho,pz,y1,
 z1,     bx,bz,by,h,lu,ln,ih;for(j=0;j<H;j++){py       =-((j+.5)/H*2-
1);       ln='';ih=     false;for(i=0;i<W;i++){         px=(i+.5)/W*2-
1;r2     =px*px+py*        py;ch=' ';ho=false;if        (r2<=1){pz=
Math    .sqrt(1-r2         );y1=py*ct-pz*st;z1=py*    st+pz*ct;bx=px*
ca-z1*sa;bz=px*sa+         z1*ca;by=y1;for(h=0;h<N;h++){if(bx*P[h][0]+
by*P[h][1]+bz*P[h][2     ]>C){ho=true;break}}lu=px*L[0]+py*L[1]+pz*L[
2];if(lu<0)lu=0;lu=.15+.85*lu;ch=ho?       (lu>.6?'o':'.'):Q[   Math.
 min(7,Math.floor(lu*7+.5))]}if(ho          !==ih){ln+=ih?       /**/
 /**/  '</span>':'<span '+/*#####*/         'class="hole">'      ;ih=
  ho}   ln+=ch}if(ih)ln+='</span>';o       +=ln+'\n'}return    o}
   /**/  function    M(p,o){o=o||{};var s=o.speed||.05,f=o.fps||12,
    a=.6  ,l=0;p        .innerHTML=F(a);if(rm)return;function S(
     ts){if(!p.          isConnected)return;if(ts-l>1000/f){a+=s;
       p.innerHTML       =F(a);l=ts}/*---*/       /*:::::::::*/
         requestAnimationFrame(S)}/*---*/         /*:::::::*/
           requestAnimationFrame(S)}/*:*/       return{/**/
             mount:M,frame:F}})();/*:::::::::::::::::::*/
                /*:::::*/  /*:*/      /*::::::::::::*/
                    /*::::::::::::::::::::::::::*/
                          /*::::::::::::::*/
